import os
import re
import asyncio
from typing import Optional

import httpx
import pandas as pd

POLYGON_BASE = "https://api.polygon.io"
CNBC_QUOTE = "https://quote.cnbc.com/quote-html-webservice/quote.htm"
NASDAQ_QUOTE = "https://api.nasdaq.com/api/quote/{symbol}/info"
PUBLIC_QUOTE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}

TF_MAP = {
    "minute": (1, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "30m": (30, "minute"),
    "hour": (1, "hour"),
    "1h": (1, "hour"),
    "4h": (4, "hour"),
    "day": (1, "day"),
    "daily": (1, "day"),
    "week": (1, "week"),
    "weekly": (1, "week"),
}

TICKER_RE = re.compile(r"^[A-Z0-9.^-]{1,15}$")

def normalize_timeframe(timeframe: str) -> str:
    tf = (timeframe or "day").lower().strip()
    if tf not in TF_MAP:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return tf

def normalize_ticker(ticker: str) -> str:
    symbol = (ticker or "").upper().strip()
    if not TICKER_RE.fullmatch(symbol):
        raise ValueError("Invalid ticker format.")
    return symbol

def _now_et() -> pd.Timestamp:
    return pd.Timestamp.now(tz="America/New_York")


def lookback_start(timeframe: str, limit: int) -> str:
    """Calendar start date that should cover `limit` bars, including weekends/holidays."""
    tf = normalize_timeframe(timeframe)
    n = max(100, min(int(limit), 50000))
    now = _now_et()
    if tf in ("week", "weekly"):
        start = now - pd.Timedelta(weeks=int(n * 1.4) + 8)
    elif tf == "4h":
        start = now - pd.Timedelta(days=int(n * 0.9) + 21)
    elif tf in ("1h", "hour"):
        start = now - pd.Timedelta(days=int(n * 0.35) + 21)
    elif tf == "30m":
        start = now - pd.Timedelta(days=int(n * 0.2) + 14)
    elif tf in ("15m", "5m", "minute"):
        start = now - pd.Timedelta(days=int(n * 0.12) + 10)
    else:
        start = now - pd.Timedelta(days=int(n * 1.8) + 14)
    return start.strftime("%Y-%m-%d")


def market_to_date() -> str:
    """Inclusive end date in ET, pushed one day ahead so today's session is not cut off."""
    return (_now_et() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def _to_epoch_ms(value) -> Optional[int]:
    if value is None or value == "":
        return None
    t = int(value)
    if t > 10**16:
        return t // 1_000_000
    if t > 10**14:
        return t // 1_000
    return t


def _snapshot_ticker(payload: Optional[dict]) -> dict:
    if not payload:
        return {}
    ticker = payload.get("ticker")
    if isinstance(ticker, dict):
        return ticker
    return payload


def bars_from_results(results: list) -> pd.DataFrame:
    df = pd.DataFrame(results).rename(columns={
        "o": "open", "h": "high", "l": "low", "c": "close",
        "v": "volume", "t": "timestamp", "vw": "provider_vwap"
    })
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Provider response missing columns: {', '.join(missing)}")

    keep = [c for c in required + ["provider_vwap"] if c in df.columns]
    df = df[keep].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True, errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=required).sort_values("timestamp").reset_index(drop=True)


def apply_live_snapshot(
    df: pd.DataFrame,
    snapshot: Optional[dict],
    timeframe: str,
    now: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Merge Polygon's current-session snapshot so the last bar is today, not the previous close."""
    if df is None or df.empty or not snapshot:
        return df

    now_et = now if now is not None else _now_et()
    if now_et.tzinfo is None:
        now_et = now_et.tz_localize("America/New_York")
    else:
        now_et = now_et.tz_convert("America/New_York")

    info = _snapshot_ticker(snapshot)
    day = info.get("day") or {}
    last_trade = info.get("lastTrade") or {}
    minute = info.get("min") or {}

    last_px = last_trade.get("p")
    if last_px is None:
        last_px = minute.get("c")
    if last_px is None:
        last_px = day.get("c")
    if last_px is None:
        return df

    last_px = float(last_px)
    if last_px <= 0:
        return df

    trade_ms = _to_epoch_ms(last_trade.get("t") or minute.get("t") or info.get("updated"))
    trade_ts = pd.to_datetime(trade_ms, unit="ms", utc=True) if trade_ms else now_et.tz_convert("UTC")
    quote_session = trade_ts.tz_convert("America/New_York").normalize()

    tf = normalize_timeframe(timeframe)
    out = df.copy()
    last_ts = out["timestamp"].iloc[-1]
    if last_ts.tzinfo is None:
        last_ts = last_ts.tz_localize("UTC")
    last_et = last_ts.tz_convert("America/New_York")

    open_ = float(day.get("o") or last_px)
    high = max(float(day.get("h") or last_px), last_px)
    low = float(day.get("l") or last_px)
    low = min(low, last_px) if low > 0 else last_px
    volume = float(day.get("v") or minute.get("av") or minute.get("v") or 0)
    vwap = day.get("vw")

    def _write_row(idx, timestamp=None):
        out.at[idx, "close"] = last_px
        out.at[idx, "high"] = max(float(out.at[idx, "high"]), high)
        out.at[idx, "low"] = min(float(out.at[idx, "low"]), low)
        if volume:
            out.at[idx, "volume"] = max(float(out.at[idx, "volume"]), volume)
        if timestamp is not None:
            out.at[idx, "timestamp"] = timestamp
        if vwap is not None and "provider_vwap" in out.columns:
            out.at[idx, "provider_vwap"] = float(vwap)

    if tf in ("day", "daily"):
        if last_et.normalize() == quote_session:
            _write_row(out.index[-1], trade_ts)
        elif last_et.normalize() < quote_session and (day.get("o") or last_trade.get("p")):
            row = {
                "timestamp": trade_ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": last_px,
                "volume": volume,
            }
            if "provider_vwap" in out.columns and vwap is not None:
                row["provider_vwap"] = float(vwap)
            out = pd.concat([out, pd.DataFrame([row])], ignore_index=True)
        return out.reset_index(drop=True)

    if tf in ("week", "weekly"):
        last_week = (last_et.isocalendar().year, last_et.isocalendar().week)
        now_week = (now_et.isocalendar().year, now_et.isocalendar().week)
        if last_week == now_week:
            _write_row(out.index[-1])
        return out.reset_index(drop=True)

    _write_row(out.index[-1])
    return out.reset_index(drop=True)

def session_bar_from_minutes(minutes: pd.DataFrame, now: Optional[pd.Timestamp] = None) -> Optional[dict]:
    """Collapse today's minute bars into a single session bar."""
    if minutes is None or minutes.empty:
        return None
    now_et = now if now is not None else _now_et()
    if now_et.tzinfo is None:
        now_et = now_et.tz_localize("America/New_York")
    else:
        now_et = now_et.tz_convert("America/New_York")
    session_date = now_et.normalize()
    ts = pd.to_datetime(minutes["timestamp"], utc=True)
    today = minutes.loc[ts.dt.tz_convert("America/New_York").dt.normalize() == session_date].copy()
    if today.empty:
        return None
    today = today.sort_values("timestamp")
    return {
        "timestamp": today["timestamp"].iloc[-1],
        "open": float(today["open"].iloc[0]),
        "high": float(today["high"].max()),
        "low": float(today["low"].min()),
        "close": float(today["close"].iloc[-1]),
        "volume": float(today["volume"].sum()),
    }


def apply_session_bar(df: pd.DataFrame, bar: Optional[dict], timeframe: str, now: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    if not bar:
        return df
    ts = pd.Timestamp(bar["timestamp"])
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    ms = int(ts.timestamp() * 1000)
    snap = {
        "ticker": {
            "day": {
                "o": bar["open"], "h": bar["high"], "l": bar["low"],
                "c": bar["close"], "v": bar["volume"],
            },
            "lastTrade": {"p": bar["close"], "t": ms},
        }
    }
    return apply_live_snapshot(df, snap, timeframe, now=now)


def apply_intraday_minutes(
    df: pd.DataFrame,
    minutes: pd.DataFrame,
    timeframe: str,
    now: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    tf = normalize_timeframe(timeframe)
    rule = "4h" if tf == "4h" else ("1h" if tf in ("1h", "hour") else None)
    if rule is None or minutes is None or minutes.empty:
        return apply_session_bar(df, session_bar_from_minutes(minutes, now=now), tf, now=now)

    s = minutes.copy()
    s["timestamp"] = pd.to_datetime(s["timestamp"], utc=True)
    s = s.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
    s = s.tz_convert("America/New_York")
    ohlc = s.resample(rule).agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna(how="any")
    if ohlc.empty:
        return df
    ohlc.index = ohlc.index.tz_convert("UTC")
    ohlc = ohlc.reset_index().rename(columns={"index": "timestamp"})
    last_ts = df["timestamp"].iloc[-1]
    if getattr(last_ts, "tzinfo", None) is None:
        last_ts = pd.Timestamp(last_ts, tz="UTC")
    new_bars = ohlc[ohlc["timestamp"] > last_ts]
    if new_bars.empty:
        return apply_session_bar(df, session_bar_from_minutes(minutes, now=now), tf, now=now)
    out = pd.concat([df, new_bars], ignore_index=True)
    return out.sort_values("timestamp").reset_index(drop=True)


def _parse_money(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("$", "").replace(",", "").strip()
    if text in {"", "--", "N/A", "n/a", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def live_quote_from_cnbc(payload: dict) -> Optional[dict]:
    node = ((payload or {}).get("QuickQuoteResult") or {}).get("QuickQuote")
    if isinstance(node, list):
        node = node[0] if node else None
    if not isinstance(node, dict):
        return None
    last = _parse_money(node.get("last"))
    if last is None or last <= 0:
        return None
    ms = _to_epoch_ms(node.get("last_time_msec"))
    if ms:
        ts = pd.to_datetime(ms, unit="ms", utc=True)
    else:
        ts = pd.to_datetime(node.get("last_time"), utc=True, errors="coerce")
    if ts is None or pd.isna(ts):
        ts = _now_et().tz_convert("UTC")
    elif ts.tzinfo is None:
        ts = ts.tz_localize("America/New_York").tz_convert("UTC")
    high = _parse_money(node.get("high")) or last
    low = _parse_money(node.get("low")) or last
    return {
        "bar": {
            "timestamp": ts,
            "open": _parse_money(node.get("open")) or last,
            "high": max(high, last),
            "low": min(low, last) if low > 0 else last,
            "close": last,
            "volume": _parse_money(node.get("fullVolume") or node.get("volume")) or 0,
        },
        "minutes": pd.DataFrame(),
        "source": "cnbc",
    }


def live_quote_from_nasdaq(payload: dict) -> Optional[dict]:
    data = (payload or {}).get("data") or {}
    primary = data.get("primaryData") or {}
    last = _parse_money(primary.get("lastSalePrice"))
    if last is None or last <= 0:
        return None
    stamp = str(primary.get("lastTradeTimestamp") or "").replace(" ET", "").strip()
    ts = pd.to_datetime(stamp, errors="coerce")
    if ts is None or pd.isna(ts):
        ts = _now_et()
    elif ts.tzinfo is None:
        ts = ts.tz_localize("America/New_York")
    ts = ts.tz_convert("UTC")
    return {
        "bar": {
            "timestamp": ts,
            "open": last,
            "high": last,
            "low": last,
            "close": last,
            "volume": _parse_money(primary.get("volume")) or 0,
        },
        "minutes": pd.DataFrame(),
        "source": "nasdaq",
    }


async def fetch_public_live_quote(client: httpx.AsyncClient, symbol: str) -> Optional[dict]:
    symbol = normalize_ticker(symbol)
    cnbc_params = {
        "partnerId": "2",
        "requestMethod": "quick",
        "exthrs": "1",
        "noform": "1",
        "output": "json",
        "symbols": symbol,
    }
    nasdaq_urls = [
        (NASDAQ_QUOTE.format(symbol=symbol), {"assetclass": "stocks"}),
        (NASDAQ_QUOTE.format(symbol=symbol), {"assetclass": "etf"}),
    ]
    try:
        cnbc_resp, nd_stock, nd_etf = await asyncio.gather(
            client.get(CNBC_QUOTE, params=cnbc_params, headers=PUBLIC_QUOTE_HEADERS),
            client.get(nasdaq_urls[0][0], params=nasdaq_urls[0][1], headers=PUBLIC_QUOTE_HEADERS),
            client.get(nasdaq_urls[1][0], params=nasdaq_urls[1][1], headers=PUBLIC_QUOTE_HEADERS),
            return_exceptions=True,
        )
    except httpx.HTTPError:
        return None

    quote = None
    if not isinstance(cnbc_resp, Exception) and getattr(cnbc_resp, "status_code", 500) < 400:
        try:
            quote = live_quote_from_cnbc(cnbc_resp.json())
        except ValueError:
            quote = None

    nasdaq_quote = None
    for resp in (nd_stock, nd_etf):
        if isinstance(resp, Exception) or getattr(resp, "status_code", 500) >= 400:
            continue
        try:
            nasdaq_quote = live_quote_from_nasdaq(resp.json())
        except ValueError:
            nasdaq_quote = None
        if nasdaq_quote:
            break

    if quote and nasdaq_quote:
        last = float(nasdaq_quote["bar"]["close"])
        quote["bar"]["close"] = last
        quote["bar"]["high"] = max(float(quote["bar"]["high"]), last)
        quote["bar"]["low"] = min(float(quote["bar"]["low"]), last)
        quote["bar"]["timestamp"] = nasdaq_quote["bar"]["timestamp"]
        quote["source"] = "cnbc+nasdaq"
        return quote
    return quote or nasdaq_quote


def _polygon_error_message(status_code: int, payload: Optional[dict] = None) -> str:
    detail = ""
    if payload:
        detail = str(payload.get("error") or payload.get("message") or "").strip()
    if status_code in (401, 403) or "api key" in detail.lower():
        return "Polygon.io rechazó la API key. Revisa POLYGON_API_KEY en .env."
    if status_code == 429:
        return "Polygon.io alcanzó el límite de peticiones. Espera un momento e inténtalo de nuevo."
    if detail:
        return f"Polygon.io: {detail}"
    return f"Polygon.io HTTP {status_code}."

async def fetch_polygon_ohlcv(ticker: str, timeframe: str = "day", limit: int = 500) -> pd.DataFrame:
    api_key = os.getenv("POLYGON_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY is not configured.")

    symbol = normalize_ticker(ticker)
    tf = normalize_timeframe(timeframe)
    mult, span = TF_MAP[tf]

    # Request the most recent bars. Polygon sorts descending, then we reverse
    # locally so all indicator calculations run chronologically.
    from_date = lookback_start(tf, limit)
    to_date = market_to_date()
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{symbol}/range/{mult}/{span}/{from_date}/{to_date}"
    requested_limit = max(100, min(int(limit), 50000))
    params = {
        "adjusted": "true",
        "sort": "desc",
        "limit": requested_limit,
        "apiKey": api_key,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(timeout=25) as client:
        aggs_resp, live_quote = await asyncio.gather(
            client.get(url, params=params, headers=headers),
            fetch_public_live_quote(client, symbol),
            return_exceptions=True,
        )

    if isinstance(aggs_resp, Exception):
        raise RuntimeError("No se pudo contactar Polygon.io para OHLCV.")
    r = aggs_resp
    payload = None
    try:
        payload = r.json()
    except ValueError:
        payload = None
    if r.status_code >= 400:
        raise RuntimeError(_polygon_error_message(r.status_code, payload if isinstance(payload, dict) else None))
    if not isinstance(payload, dict):
        raise RuntimeError("Polygon.io returned an invalid JSON payload.")

    status = str(payload.get("status") or "").upper()
    if status in {"ERROR", "NOT_AUTHORIZED"}:
        raise RuntimeError(_polygon_error_message(r.status_code, payload))

    results = payload.get("results") or []
    if not results:
        raise ValueError(f"Polygon.io no devolvió OHLCV para {symbol}.")

    df = bars_from_results(results).tail(requested_limit).reset_index(drop=True)

    now_et = _now_et()
    last_before = df["timestamp"].iloc[-1]
    live_source = None
    if isinstance(live_quote, Exception):
        live_quote = None
    if live_quote and live_quote.get("bar"):
        live_source = live_quote.get("source") or "public"
        if tf in ("1h", "hour", "4h") and live_quote.get("minutes") is not None and not live_quote["minutes"].empty:
            df = apply_intraday_minutes(df, live_quote["minutes"], tf, now=now_et)
        df = apply_session_bar(df, live_quote["bar"], tf, now=now_et)

    session_applied = str(pd.Timestamp(df["timestamp"].iloc[-1])) != str(last_before)
    last_et = pd.Timestamp(df["timestamp"].iloc[-1])
    if last_et.tzinfo is None:
        last_et = last_et.tz_localize("UTC")
    if last_et.tz_convert("America/New_York").normalize() == now_et.normalize():
        session_applied = True

    df = df.sort_values("timestamp").tail(requested_limit).reset_index(drop=True)
    df.attrs["live"] = {
        "session_date": now_et.strftime("%Y-%m-%d"),
        "session_applied": session_applied,
        "quote_source": live_source,
    }

    if len(df) < 30:
        raise ValueError("Not enough bars to calculate institutional levels.")
    if (df[["open","high","low","close"]] <= 0).any().any():
        raise ValueError("Invalid non-positive market prices returned by provider.")
    if (df["volume"] < 0).any():
        raise ValueError("Invalid negative volume returned by provider.")

    return df