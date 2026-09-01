"""Cotización, velas y cadena de opciones para Premium Income."""
import os
import time
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from app.data_provider import fetch_polygon_ohlcv, normalize_ticker, polygon_api_key

from .demo_options import build_illustrative_chain
from .schemas import (
    Bar,
    BarsResponse,
    CorporateEventsResponse,
    DataSourceStatus,
    DividendEvent,
    MarketSessionStatus,
    OptionChainResponse,
    OptionContract,
    Quote,
    SplitEvent,
)

NY_TZ = ZoneInfo("America/New_York")
POLYGON_BASE = "https://api.polygon.io"


def _now_ny() -> datetime:
    return datetime.now(NY_TZ)


def _session() -> MarketSessionStatus:
    now_ny = _now_ny()
    if now_ny.weekday() >= 5:
        return MarketSessionStatus.CLOSED
    minutes = now_ny.hour * 60 + now_ny.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return MarketSessionStatus.PRE_MARKET
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return MarketSessionStatus.OPEN
    if 16 * 60 <= minutes < 20 * 60:
        return MarketSessionStatus.AFTER_HOURS
    return MarketSessionStatus.CLOSED


def quote_and_bars_from_df(ticker: str, df: pd.DataFrame) -> tuple:
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    vols = df["volume"].astype(float)
    avg_vol = float(vols.tail(20).mean()) if len(vols) else 0.0
    last_vol = float(last.get("volume") or 0)
    rel_vol = round(last_vol / avg_vol, 2) if avg_vol else None
    price = float(last["close"])
    prev_close = float(prev["close"])
    change = price - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0.0
    high_52 = float(df["high"].tail(252).max()) if len(df) else price
    low_52 = float(df["low"].tail(252).min()) if len(df) else price
    ts = pd.Timestamp(last["timestamp"])
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    now_ny = _now_ny()
    quote = Quote(
        ticker=ticker,
        price=round(price, 4),
        change=round(change, 4),
        change_percent=round(change_pct, 4),
        volume=int(last.get("volume") or 0),
        relative_volume=rel_vol,
        day_high=float(last["high"]),
        day_low=float(last["low"]),
        week52_high=high_52,
        week52_low=low_52,
        market_session=_session(),
        data_source_status=DataSourceStatus.LIVE,
        updated_at_utc=ts.to_pydatetime(),
        updated_at_ny=now_ny.strftime("%Y-%m-%d %H:%M:%S %Z"),
        is_demo=False,
    )
    bars = []
    for _, row in df.iterrows():
        stamp = pd.Timestamp(row["timestamp"])
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        bars.append(
            Bar(
                timestamp_utc=stamp.to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row.get("volume") or 0),
            )
        )
    bars_resp = BarsResponse(
        ticker=ticker,
        timeframe="1d",
        bars=bars,
        data_source_status=DataSourceStatus.LIVE,
        is_demo=False,
        updated_at_ny=quote.updated_at_ny,
    )
    return quote, bars_resp


def _parse_option_row(ticker: str, payload: dict, updated_at_ny: str) -> Optional[OptionContract]:
    details = payload.get("details") or {}
    greeks = payload.get("greeks") or {}
    last_quote = payload.get("last_quote") or {}
    day = payload.get("day") or {}
    exp_str = details.get("expiration_date")
    occ_symbol = details.get("ticker")
    strike = details.get("strike_price")
    contract_type = details.get("contract_type")
    if not (exp_str and occ_symbol and strike is not None and contract_type in ("call", "put")):
        return None
    expiration_date = date.fromisoformat(exp_str)
    bid = last_quote.get("bid")
    ask = last_quote.get("ask")
    mid = round((bid + ask) / 2, 4) if (bid is not None and ask is not None) else None
    spread_dollars = round(ask - bid, 4) if (bid is not None and ask is not None) else None
    spread_percent = round((spread_dollars / mid) * 100, 2) if (spread_dollars is not None and mid) else None
    return OptionContract(
        occ_symbol=occ_symbol,
        underlying_ticker=ticker,
        contract_type=contract_type,
        strike=strike,
        expiration_date=expiration_date,
        dte=(expiration_date - date.today()).days,
        bid=bid,
        ask=ask,
        mid=mid,
        last=day.get("close"),
        volume=day.get("volume"),
        open_interest=payload.get("open_interest"),
        delta=greeks.get("delta"),
        gamma=greeks.get("gamma"),
        theta=greeks.get("theta"),
        vega=greeks.get("vega"),
        implied_volatility=payload.get("implied_volatility"),
        spread_dollars=spread_dollars,
        spread_percent=spread_percent,
        data_source_status=DataSourceStatus.LIVE,
        updated_at_ny=updated_at_ny,
        is_demo=False,
    )


async def _polygon_get(client: httpx.AsyncClient, path: str, params: Optional[dict] = None) -> Optional[dict]:
    api_key = polygon_api_key()
    if not api_key:
        return None
    request_params = dict(params or {})
    request_params["apiKey"] = api_key
    try:
        resp = await client.get(f"{POLYGON_BASE}{path}", params=request_params, timeout=20)
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


async def fetch_option_chain(client: httpx.AsyncClient, ticker: str, spot: float) -> tuple:
    data = await _polygon_get(client, f"/v3/snapshot/options/{ticker}", {"limit": 250})
    results = (data or {}).get("results") or []
    if not results:
        chain = build_illustrative_chain(ticker, spot)
        return chain, False
    now_ny = _now_ny().strftime("%Y-%m-%d %H:%M:%S %Z")
    contracts = []
    for row in results:
        parsed = _parse_option_row(ticker, row, now_ny)
        if parsed and parsed.dte >= 0:
            contracts.append(parsed)
    if not contracts:
        return build_illustrative_chain(ticker, spot), False
    return (
        OptionChainResponse(
            ticker=ticker,
            contracts=contracts,
            data_source_status=DataSourceStatus.LIVE,
            is_demo=False,
            updated_at_ny=now_ny,
        ),
        True,
    )


async def fetch_events(client: httpx.AsyncClient, ticker: str) -> CorporateEventsResponse:
    dividends_data = await _polygon_get(
        client,
        "/v3/reference/dividends",
        {"ticker": ticker, "limit": 10, "order": "desc", "sort": "ex_dividend_date"},
    )
    splits_data = await _polygon_get(client, "/v3/reference/splits", {"ticker": ticker, "limit": 5})
    now_ny = _now_ny().strftime("%Y-%m-%d %H:%M:%S %Z")
    dividends = []
    for item in (dividends_data or {}).get("results") or []:
        if "ex_dividend_date" in item and "cash_amount" in item:
            dividends.append(
                DividendEvent(
                    ex_dividend_date=item["ex_dividend_date"],
                    pay_date=item.get("pay_date"),
                    declaration_date=item.get("declaration_date"),
                    record_date=item.get("record_date"),
                    cash_amount=item["cash_amount"],
                )
            )
    splits = []
    for item in (splits_data or {}).get("results") or []:
        if "execution_date" in item:
            splits.append(
                SplitEvent(
                    execution_date=item["execution_date"],
                    split_from=item["split_from"],
                    split_to=item["split_to"],
                )
            )
    live = dividends_data is not None or splits_data is not None
    return CorporateEventsResponse(
        ticker=ticker,
        dividends=dividends,
        splits=splits,
        next_earnings_date=None,
        earnings_available=False,
        earnings_note="Polygon no incluye calendario de earnings en el plan de acciones estándar.",
        data_source_status=DataSourceStatus.LIVE if live else DataSourceStatus.DEMO,
        is_demo=not live,
        updated_at_ny=now_ny,
    )


class MarketSnapshot:
    def __init__(self, quote: Quote, bars: BarsResponse, events: CorporateEventsResponse, chain: OptionChainResponse, options_live: bool):
        self.quote = quote
        self.bars = bars
        self.events = events
        self.chain = chain
        self.options_live = options_live


_SNAPSHOTS = {}
_SNAPSHOT_TTL = 90


async def load_market(ticker: str) -> MarketSnapshot:
    symbol = normalize_ticker(ticker)
    now = time.time()
    cached = _SNAPSHOTS.get(symbol)
    if cached and now - cached[0] < _SNAPSHOT_TTL:
        return cached[1]
    df = await fetch_polygon_ohlcv(symbol, timeframe="day", limit=300)
    quote, bars = quote_and_bars_from_df(symbol, df)
    async with httpx.AsyncClient(timeout=25) as client:
        events = await fetch_events(client, symbol)
        chain, options_live = await fetch_option_chain(client, symbol, quote.price)
    snap = MarketSnapshot(quote, bars, events, chain, options_live)
    _SNAPSHOTS[symbol] = (now, snap)
    return snap
