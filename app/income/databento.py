"""Cadena de opciones vía Databento Historical HTTP API (OPRA.PILLAR)."""
import asyncio
import json
import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("neurotrader.databento")

NY_TZ = ZoneInfo("America/New_York")
DATABENTO_HIST = "https://hist.databento.com/v0"
DATASET = "OPRA.PILLAR"
DEFINITIONS_TTL_SECONDS = 10 * 60
RANGE_TTL_SECONDS = 5 * 60
QUOTE_LOOKBACK = timedelta(minutes=20)
SYMBOL_BATCH = 2000
UNDEF_PRICE = 9223372036854775807
STAT_OPEN_INTEREST = 9

_DEFINITIONS_CACHE = {}  # type: Dict[str, Tuple[float, Dict[str, Any]]]
_RANGE_CACHE = {}  # type: Dict[str, Tuple[float, Dict[str, Any]]]


class DatabentoUnavailable(Exception):
    def __init__(self, code, message=""):
        self.code = code
        super().__init__(message or code)


def _api_key():
    return (os.getenv("DATABENTO_API_KEY") or "").strip()


def _today_ny():
    return datetime.now(NY_TZ).date()


def _num(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _positive_price(value):
    number = _num(value)
    if number is None or number <= 0:
        return None
    return number


def _int_or_none(value):
    number = _num(value)
    if number is None:
        return None
    return int(number)


def compute_dte(expiration, today=None):
    if today is None:
        today = _today_ny()
    return (expiration - today).days


def parse_expiration_date(raw, tz=NY_TZ):
    if raw is None:
        return None
    if isinstance(raw, datetime):
        stamp = raw
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        utc = stamp.astimezone(timezone.utc)
        if utc.hour == 0 and utc.minute == 0 and utc.second == 0:
            return utc.date()
        return stamp.astimezone(tz).date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, (int, float)):
        number = float(raw)
        if number > 1e15:
            number = number / 1e9
        try:
            return parse_expiration_date(datetime.fromtimestamp(number, tz=timezone.utc), tz=tz)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(raw).strip()
    if not text or text.lower() in ("nat", "none", "null"):
        return None
    if text.isdigit():
        return parse_expiration_date(int(text), tz=tz)
    try:
        if "T" in text:
            return parse_expiration_date(_parse_iso(text), tz=tz)
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_iso(text):
    text = str(text).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        digits = ""
        tz_part = ""
        for index, char in enumerate(rest):
            if char.isdigit():
                digits += char
            else:
                tz_part = rest[index:]
                break
        text = head + "." + digits[:6].ljust(6, "0") + tz_part
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def parse_px(value):
    number = _num(value)
    if number is None:
        return None
    if number >= UNDEF_PRICE * 0.99:
        return None
    if abs(number) >= 1e12:
        number = number / 1e9
    if number <= 0:
        return None
    return number


def resolve_mid(bid, ask, mid=None):
    provided = _positive_price(mid)
    if provided is not None:
        return provided
    if bid is not None and ask is not None:
        computed = (bid + ask) / 2.0
        if computed > 0:
            return round(computed, 6)
    return None


def spread_percent(bid, ask, mid=None):
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0:
        return None
    mark = resolve_mid(bid, ask, mid)
    if mark is None or mark <= 0:
        return None
    return round(((ask - bid) / mark) * 100.0, 2)


def nearest_expiration(expirations, requested_dte):
    if not expirations:
        return None
    return min(
        expirations,
        key=lambda item: (abs(item["dte"] - requested_dte), item["dte"], item["expiration"]),
    )


def build_expiration_list(dates, today=None):
    if today is None:
        today = _today_ny()
    out = []
    for exp in sorted(set(dates)):
        dte = compute_dte(exp, today)
        if dte < 0:
            continue
        out.append({"expiration": exp.isoformat(), "dte": dte})
    return out


def occ_symbol(raw):
    text = str(raw or "").strip()
    if not text:
        return ""
    return " ".join(text.split())


def instrument_side(raw):
    value = str(raw or "").strip().upper()
    if value in ("C", "CALL"):
        return "CALL"
    if value in ("P", "PUT"):
        return "PUT"
    return None


def is_deleted(record):
    action = str(record.get("security_update_action") or "").strip().upper()
    return action in ("D", "DELETE", "DELETED")


def extract_bbo(record):
    levels = record.get("levels")
    if isinstance(levels, list) and levels:
        top = levels[0] or {}
        bid = parse_px(top.get("bid_px", top.get("bid_px_00")))
        ask = parse_px(top.get("ask_px", top.get("ask_px_00")))
        return bid, ask
    bid = parse_px(record.get("bid_px_00", record.get("pretty_bid_px_00", record.get("bid_px"))))
    ask = parse_px(record.get("ask_px_00", record.get("pretty_ask_px_00", record.get("ask_px"))))
    return bid, ask


def record_symbol(record):
    return occ_symbol(record.get("symbol") or record.get("raw_symbol") or "")


def parse_jsonl(text):
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line[0] not in "{[":
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def parse_timestamp(raw):
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        stamp = raw
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    if isinstance(raw, (int, float)):
        number = float(raw)
        if number > 1e15:
            number = number / 1e9
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return _parse_iso(text).astimezone(timezone.utc)
    except ValueError:
        return None


def classify_data_status(updated_at, today=None):
    """Historical HTTP is never real-time. OPRA without a live license is delayed or T+1."""
    if today is None:
        today = _today_ny()
    if updated_at is None:
        return "delayed"
    stamp = parse_timestamp(updated_at)
    if stamp is None:
        return "delayed"
    if stamp.astimezone(NY_TZ).date() < today:
        return "historical"
    return "delayed"


def format_updated(updated_at):
    stamp = parse_timestamp(updated_at)
    if stamp is None:
        return None
    return stamp.astimezone(NY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def quote_window(dataset_end):
    end = parse_timestamp(dataset_end) or datetime.now(timezone.utc)
    start = end - QUOTE_LOOKBACK
    return start.isoformat(), end.isoformat()


def session_date_from_range(payload, today=None):
    if today is None:
        today = _today_ny()
    schemas = payload.get("schema") or {}
    for key in ("cbbo-1m", "cbbo-1s", "definition", "statistics"):
        block = schemas.get(key) or {}
        stamp = parse_timestamp(block.get("end"))
        if stamp is not None:
            return stamp.astimezone(NY_TZ).date(), stamp
    stamp = parse_timestamp(payload.get("end"))
    if stamp is not None:
        return stamp.astimezone(NY_TZ).date(), stamp
    return today, datetime.now(timezone.utc)


def parse_definition_records(rows):
    instruments = {}
    for row in rows:
        if is_deleted(row):
            symbol = record_symbol(row)
            if symbol:
                instruments.pop(symbol, None)
            continue
        side = instrument_side(row.get("instrument_class"))
        strike = parse_px(row.get("strike_price"))
        expiration = parse_expiration_date(row.get("expiration"))
        symbol = record_symbol(row)
        if side is None or strike is None or expiration is None or not symbol:
            continue
        instruments[symbol] = {
            "symbol": symbol,
            "type": side,
            "strike": strike,
            "expiration": expiration.isoformat(),
            "expiration_date": expiration,
        }
    return instruments


def latest_quotes(rows):
    quotes = {}
    for row in rows:
        symbol = record_symbol(row)
        if not symbol:
            continue
        bid, ask = extract_bbo(row)
        quotes[symbol] = {
            "bid": bid,
            "ask": ask,
            "updated": row.get("ts_recv") or row.get("ts_event"),
        }
    return quotes


def latest_open_interest(rows):
    interest = {}
    for row in rows:
        stat_type = row.get("stat_type")
        if stat_type not in (STAT_OPEN_INTEREST, str(STAT_OPEN_INTEREST), "open_interest", "OPEN_INTEREST"):
            continue
        symbol = record_symbol(row)
        quantity = _int_or_none(row.get("quantity"))
        if not symbol or quantity is None or quantity < 0:
            continue
        interest[symbol] = quantity
    return interest


def latest_volume(rows):
    volume = {}
    for row in rows:
        symbol = record_symbol(row)
        amount = _int_or_none(row.get("volume"))
        if not symbol or amount is None or amount < 0:
            continue
        volume[symbol] = amount
    return volume


def normalize_contract(instrument, quote=None, open_interest=None, volume=None, today=None):
    bid = None if not quote else quote.get("bid")
    ask = None if not quote else quote.get("ask")
    bid = _positive_price(bid)
    ask = _positive_price(ask)
    mid = resolve_mid(bid, ask, None)
    expiration = instrument["expiration"]
    expiration_date = instrument.get("expiration_date") or parse_expiration_date(expiration)
    dte = compute_dte(expiration_date, today) if expiration_date else None
    return {
        "symbol": instrument["symbol"],
        "type": instrument["type"],
        "strike": instrument["strike"],
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "delta": None,
        "impliedVolatility": None,
        "openInterest": open_interest,
        "expiration": expiration,
        "dte": dte,
        "spreadPercent": spread_percent(bid, ask, mid),
        "volume": volume,
        "updated": None if not quote else quote.get("updated"),
    }


def unavailable_payload(ticker, requested_dte, expirations=None):
    return {
        "ticker": ticker,
        "requestedDte": requested_dte,
        "actualDte": None,
        "expiration": None,
        "updated": None,
        "live": False,
        "ok": False,
        "dataStatus": "unavailable",
        "expirations": expirations or [],
        "contracts": [],
    }


def _chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


async def _databento_request(client, path, params, allow_empty=False):
    key = _api_key()
    if not key:
        logger.warning("Databento request skipped: DATABENTO_API_KEY is not configured")
        raise DatabentoUnavailable("missing_token")
    url = DATABENTO_HIST + path
    try:
        response = await client.post(
            url,
            data=params,
            auth=(key, ""),
            headers={"Accept": "application/json, text/plain, */*"},
        )
    except httpx.HTTPError:
        logger.exception("Databento network error path=%s", path)
        raise DatabentoUnavailable("network")
    if response.status_code == 204 and allow_empty:
        return []
    if response.status_code == 401:
        logger.warning("Databento unauthorized path=%s", path)
        raise DatabentoUnavailable("unauthorized")
    if response.status_code in (402, 403):
        logger.warning("Databento license/plan error status=%s path=%s", response.status_code, path)
        raise DatabentoUnavailable("payment_required")
    if response.status_code == 429:
        logger.warning("Databento rate-limited path=%s", path)
        raise DatabentoUnavailable("rate_limit")
    if response.status_code == 422:
        logger.info("Databento no data path=%s params=%s body=%s", path, params, response.text[:300])
        if allow_empty:
            return []
        raise DatabentoUnavailable("no_data")
    if response.status_code != 200:
        logger.warning(
            "Databento error status=%s path=%s body=%s",
            response.status_code,
            path,
            response.text[:300],
        )
        raise DatabentoUnavailable("http_%s" % response.status_code)
    return parse_jsonl(response.text)


async def _databento_get(client, path, params):
    key = _api_key()
    if not key:
        logger.warning("Databento request skipped: DATABENTO_API_KEY is not configured")
        raise DatabentoUnavailable("missing_token")
    try:
        response = await client.get(
            DATABENTO_HIST + path,
            params=params,
            auth=(key, ""),
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError:
        logger.exception("Databento network error path=%s", path)
        raise DatabentoUnavailable("network")
    if response.status_code == 401:
        raise DatabentoUnavailable("unauthorized")
    if response.status_code in (402, 403):
        raise DatabentoUnavailable("payment_required")
    if response.status_code == 429:
        raise DatabentoUnavailable("rate_limit")
    if response.status_code != 200:
        logger.warning(
            "Databento error status=%s path=%s body=%s",
            response.status_code,
            path,
            response.text[:300],
        )
        raise DatabentoUnavailable("http_%s" % response.status_code)
    try:
        return response.json()
    except ValueError:
        raise DatabentoUnavailable("invalid_json")


def _range_params(schema, symbols, start, end=None, stype_in="raw_symbol"):
    params = {
        "dataset": DATASET,
        "schema": schema,
        "symbols": ",".join(symbols) if isinstance(symbols, list) else symbols,
        "stype_in": stype_in,
        "stype_out": "raw_symbol",
        "encoding": "json",
        "compression": "none",
        "pretty_px": "true",
        "pretty_ts": "true",
        "map_symbols": "true",
        "start": start,
    }
    if end:
        params["end"] = end
    return params


async def fetch_dataset_range(client):
    now = time.time()
    cached = _RANGE_CACHE.get(DATASET)
    if cached and now - cached[0] < RANGE_TTL_SECONDS:
        return cached[1]
    payload = await _databento_get(client, "/metadata.get_dataset_range", {"dataset": DATASET})
    _RANGE_CACHE[DATASET] = (now, payload or {})
    return payload or {}


async def fetch_definitions(client, ticker, session_date, today=None):
    now = time.time()
    cache_key = "%s:%s" % (ticker, session_date.isoformat())
    cached = _DEFINITIONS_CACHE.get(cache_key)
    if cached and now - cached[0] < DEFINITIONS_TTL_SECONDS:
        return cached[1]
    rows = await _databento_request(
        client,
        "/timeseries.get_range",
        _range_params(
            "definition",
            "%s.OPT" % ticker,
            session_date.isoformat(),
            stype_in="parent",
        ),
        allow_empty=True,
    )
    instruments = parse_definition_records(rows)
    expirations = build_expiration_list(
        [item["expiration_date"] for item in instruments.values()],
        today=today,
    )
    payload = {"instruments": instruments, "expirations": expirations}
    _DEFINITIONS_CACHE[cache_key] = (now, payload)
    return payload


async def _batched_schema(client, schema, symbols, start, end=None):
    merged = []
    if not symbols:
        return merged
    for group in _chunks(symbols, SYMBOL_BATCH):
        rows = await _databento_request(
            client,
            "/timeseries.get_range",
            _range_params(schema, group, start, end=end, stype_in="raw_symbol"),
            allow_empty=True,
        )
        merged.extend(rows)
    return merged


async def get_option_chain_for_dte(ticker, requested_dte, today=None):
    if today is None:
        today = _today_ny()
    requested_dte = int(requested_dte)
    if requested_dte < 0:
        requested_dte = 0
    ticker = str(ticker or "").upper().strip()
    async with httpx.AsyncClient(timeout=45) as client:
        try:
            dataset_range = await fetch_dataset_range(client)
        except DatabentoUnavailable:
            return unavailable_payload(ticker, requested_dte)
        session_date, dataset_end = session_date_from_range(dataset_range, today=today)
        try:
            definition_payload = await fetch_definitions(client, ticker, session_date, today=today)
        except DatabentoUnavailable:
            return unavailable_payload(ticker, requested_dte)
        expirations = definition_payload.get("expirations") or []
        instruments = definition_payload.get("instruments") or {}
        if not expirations or not instruments:
            logger.info("Databento has no option definitions for %s on %s", ticker, session_date)
            return unavailable_payload(ticker, requested_dte)
        chosen = nearest_expiration(expirations, requested_dte)
        if chosen is None:
            return unavailable_payload(ticker, requested_dte, expirations)
        selected = [
            item for item in instruments.values()
            if item["expiration"] == chosen["expiration"]
        ]
        symbols = [item["symbol"] for item in selected]
        start, end = quote_window(dataset_end)
        quotes = {}
        open_interest = {}
        volume = {}
        try:
            quote_rows, stat_rows, volume_rows = await _gather_chain_extras(
                client, symbols, start, end, session_date
            )
            quotes = latest_quotes(quote_rows)
            open_interest = latest_open_interest(stat_rows)
            volume = latest_volume(volume_rows)
        except DatabentoUnavailable:
            logger.warning("Databento quote/stats unavailable ticker=%s expiration=%s", ticker, chosen["expiration"])
    contracts = []
    latest_update = None
    for instrument in selected:
        symbol = instrument["symbol"]
        quote = quotes.get(symbol)
        parsed = normalize_contract(
            instrument,
            quote=quote,
            open_interest=open_interest.get(symbol),
            volume=volume.get(symbol),
            today=today,
        )
        if parsed:
            contracts.append(parsed)
            stamp = None if not quote else parse_timestamp(quote.get("updated"))
            if stamp is not None and (latest_update is None or stamp > latest_update):
                latest_update = stamp
    contracts.sort(key=lambda item: (item["strike"], 0 if item["type"] == "CALL" else 1, item["symbol"]))
    if not contracts:
        return {
            "ticker": ticker,
            "requestedDte": requested_dte,
            "actualDte": chosen["dte"],
            "expiration": chosen["expiration"],
            "updated": format_updated(latest_update),
            "live": False,
            "ok": False,
            "dataStatus": "unavailable",
            "expirations": expirations,
            "contracts": [],
        }
    data_status = classify_data_status(latest_update or dataset_end, today=today)
    return {
        "ticker": ticker,
        "requestedDte": requested_dte,
        "actualDte": chosen["dte"],
        "expiration": chosen["expiration"],
        "updated": format_updated(latest_update or dataset_end),
        "live": False,
        "ok": True,
        "dataStatus": data_status,
        "expirations": expirations,
        "contracts": contracts,
    }


async def _gather_chain_extras(client, symbols, start, end, session_date):
    quote_task = _batched_schema(client, "cbbo-1m", symbols, start, end)
    stats_task = _batched_schema(client, "statistics", symbols, session_date.isoformat())
    volume_task = _batched_schema(client, "ohlcv-1d", symbols, session_date.isoformat())
    return await asyncio.gather(quote_task, stats_task, volume_task)
