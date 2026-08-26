"""Cadena de opciones ilustrativa, anclada al precio real del subyacente."""
import hashlib
import random
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .schemas import DataSourceStatus, OptionChainResponse, OptionContract

NY_TZ = ZoneInfo("America/New_York")
_DEMO_DTE_LADDER = (7, 14, 21, 35, 49)


def _seeded_random(ticker: str) -> random.Random:
    seed = int(hashlib.sha256(ticker.encode()).hexdigest(), 16) % (10 ** 8)
    return random.Random(seed)


def _strike_ladder(spot: float, rng: random.Random):
    step_pct = rng.uniform(0.035, 0.06)
    increment = 1.0 if spot >= 50 else 0.5
    strikes = []
    for offset in range(-4, 5):
        raw = spot * (1 + offset * step_pct)
        strikes.append(round(round(raw / increment) * increment, 2))
    return sorted(set(s for s in strikes if s > 0))


def _occ_symbol(ticker: str, expiration: date, contract_type: str, strike: float) -> str:
    cp = "C" if contract_type == "call" else "P"
    strike_int = int(round(strike * 1000))
    return f"{ticker}{expiration.strftime('%y%m%d')}{cp}{strike_int:08d}"


def _demo_delta(contract_type: str, moneyness: float, rng: random.Random) -> float:
    base = max(0.02, min(0.98, 0.5 - moneyness * 1.7 + rng.uniform(-0.03, 0.03)))
    return round(base, 4) if contract_type == "call" else round(-(1 - base), 4)


def build_illustrative_chain(ticker: str, spot: float, expiration: Optional[date] = None) -> OptionChainResponse:
    ticker = ticker.upper().strip()
    opt_rng = _seeded_random(ticker + "-options")
    today = date.today()
    expirations = [today + timedelta(days=d) for d in _DEMO_DTE_LADDER]
    if expiration:
        expirations = [expiration]
    now_ny = datetime.now(NY_TZ)
    updated_at_ny = now_ny.strftime("%Y-%m-%d %H:%M:%S %Z")
    contracts = []
    for exp in expirations:
        dte = max((exp - today).days, 0)
        for strike in _strike_ladder(spot, opt_rng):
            moneyness = (strike - spot) / spot
            for contract_type in ("call", "put"):
                iv = round(opt_rng.uniform(0.18, 0.55), 4)
                bid = round(max(0.02, opt_rng.uniform(0.3, 8.0) * (1 - min(abs(moneyness), 0.9))), 2)
                ask = round(bid + opt_rng.uniform(0.02, 0.25), 2)
                mid = round((bid + ask) / 2, 2)
                contracts.append(
                    OptionContract(
                        occ_symbol=_occ_symbol(ticker, exp, contract_type, strike),
                        underlying_ticker=ticker,
                        contract_type=contract_type,
                        strike=strike,
                        expiration_date=exp,
                        dte=dte,
                        bid=bid,
                        ask=ask,
                        last=mid,
                        mid=mid,
                        volume=opt_rng.randint(0, 5000),
                        open_interest=opt_rng.randint(0, 20000),
                        delta=_demo_delta(contract_type, moneyness, opt_rng),
                        gamma=round(opt_rng.uniform(0.001, 0.05), 4),
                        theta=round(-opt_rng.uniform(0.01, 0.2), 4),
                        vega=round(opt_rng.uniform(0.01, 0.3), 4),
                        implied_volatility=iv,
                        spread_dollars=round(ask - bid, 2),
                        spread_percent=round(((ask - bid) / mid) * 100, 2) if mid else None,
                        data_source_status=DataSourceStatus.DEMO,
                        updated_at_ny=updated_at_ny,
                        is_demo=True,
                    )
                )
    return OptionChainResponse(
        ticker=ticker,
        contracts=contracts,
        data_source_status=DataSourceStatus.DEMO,
        is_demo=True,
        updated_at_ny=updated_at_ny,
    )
