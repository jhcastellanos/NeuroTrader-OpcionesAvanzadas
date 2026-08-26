from typing import Optional
"""
Motor de backtesting (seccion 14 del brief).

LIMITACION CRITICA DOCUMENTADA: no existe una fuente de datos
historicos de opciones reales integrada en esta app (ni en el plan de
Polygon usado, ni en modo demo). Este motor SIMULA primas historicas
con el modelo Black-Scholes (app/engine/black_scholes.py), usando
volatilidad historica CAUSAL — calculada solo con precios hasta el dia
de cada entrada, nunca con datos futuros, para evitar look-ahead bias.
Es una aproximacion razonable para explorar el COMPORTAMIENTO de una
estrategia a lo largo del tiempo, NUNCA un backtest con primas de
mercado reales. Ningun otro modulo de la app usa este motor.
"""
from dataclasses import dataclass

from app.income.engine.black_scholes import BSInputs, bs_price, strike_for_target_delta
from app.income.engine.indicators import historical_volatility_pct
from app.income.schemas import Bar

MIN_HISTORY_FOR_IV = 21  # dias minimos de historial antes de poder operar (evita look-ahead)


@dataclass
class BacktestCycle:
    entry_date: str
    exit_date: str
    strike: float
    entry_premium: float
    exit_premium: float
    contracts: int
    pnl: float
    assigned: bool
    closed_early: bool


@dataclass
class BacktestResult:
    cycles: list[BacktestCycle]
    total_premium: float
    total_pnl: float
    win_rate: float
    num_trades: int
    assignments: int
    max_drawdown_pct: float
    return_on_capital_pct: float
    buy_and_hold_return_pct: float
    monthly_pnl: dict[str, float]
    best_cycle_pnl: Optional[float]
    worst_cycle_pnl: Optional[float]


def _causal_iv(closes: list[float], index: int) -> Optional[float]:
    """Volatilidad historica usando SOLO precios hasta `index` (inclusive) — nunca datos futuros."""
    window = closes[: index + 1]
    hv = historical_volatility_pct(window, period=20)
    if hv is None:
        return None
    return max(hv / 100, 0.05)


def run_backtest(
    bars: list[Bar],
    *,
    strategy_type: str,
    target_delta: float,
    dte_days: int,
    profit_target_pct: float,
    initial_capital: float,
    contracts_per_cycle: int = 1,
    reinvest: bool = False,
) -> Optional[BacktestResult]:
    """
    `bars` debe venir ordenado de mas antiguo a mas reciente (mismo
    orden que devuelven los proveedores de esta app). Devuelve None si
    no hay suficiente historial para completar ni un ciclo.
    """
    if dte_days <= 0 or len(bars) < MIN_HISTORY_FOR_IV + dte_days:
        return None

    contract_type = "call" if strategy_type == "covered_call" else "put"
    closes = [b.close for b in bars]

    cycles: list[BacktestCycle] = []
    equity = initial_capital
    peak = equity
    max_drawdown = 0.0

    i = MIN_HISTORY_FOR_IV
    while i + dte_days < len(bars):
        entry_bar = bars[i]
        spot0 = entry_bar.close
        iv = _causal_iv(closes, i)
        if iv is None:
            i += 1
            continue

        strike = strike_for_target_delta(spot0, target_delta, dte_days, iv, contract_type)
        entry_premium = bs_price(
            BSInputs(spot=spot0, strike=strike, dte_days=dte_days, iv=iv, contract_type=contract_type)
        )

        contracts = contracts_per_cycle
        if reinvest and strategy_type == "cash_secured_put" and strike > 0:
            capital_needed = strike * 100
            contracts = max(1, int(equity // capital_needed))

        mid_offset = max(1, dte_days // 2)
        mid_index = i + mid_offset
        exit_index = i + dte_days
        closed_early = False
        exit_bar_index = exit_index
        exit_premium = entry_premium

        if mid_index < len(bars):
            spot_mid = bars[mid_index].close
            remaining_dte = dte_days - mid_offset
            mid_premium = bs_price(
                BSInputs(spot=spot_mid, strike=strike, dte_days=remaining_dte, iv=iv, contract_type=contract_type)
            )
            captured_pct = ((entry_premium - mid_premium) / entry_premium * 100) if entry_premium > 0 else 0
            if captured_pct >= profit_target_pct:
                exit_premium = mid_premium
                exit_bar_index = mid_index
                closed_early = True

        if not closed_early:
            final_index = min(exit_index, len(bars) - 1)
            exit_spot = bars[final_index].close
            exit_premium = bs_price(BSInputs(spot=exit_spot, strike=strike, dte_days=0, iv=iv, contract_type=contract_type))
            exit_bar_index = final_index
            assigned = exit_premium > 0
        else:
            assigned = False

        pnl = round((entry_premium - exit_premium) * 100 * contracts, 2)
        equity += pnl
        peak = max(peak, equity)
        drawdown = ((peak - equity) / peak * 100) if peak > 0 else 0
        max_drawdown = max(max_drawdown, drawdown)

        cycles.append(
            BacktestCycle(
                entry_date=entry_bar.timestamp_utc.date().isoformat(),
                exit_date=bars[min(exit_bar_index, len(bars) - 1)].timestamp_utc.date().isoformat(),
                strike=strike,
                entry_premium=entry_premium,
                exit_premium=exit_premium,
                contracts=contracts,
                pnl=pnl,
                assigned=assigned,
                closed_early=closed_early,
            )
        )

        i = exit_bar_index + 1

    if not cycles:
        return None

    total_premium = round(sum(c.entry_premium * 100 * c.contracts for c in cycles), 2)
    total_pnl = round(sum(c.pnl for c in cycles), 2)
    wins = sum(1 for c in cycles if c.pnl > 0)
    win_rate = round(wins / len(cycles) * 100, 2)
    assignments = sum(1 for c in cycles if c.assigned)

    monthly_pnl: dict[str, float] = {}
    for c in cycles:
        key = c.exit_date[:7]
        monthly_pnl[key] = round(monthly_pnl.get(key, 0.0) + c.pnl, 2)

    buy_and_hold_pct = round((closes[-1] - closes[MIN_HISTORY_FOR_IV]) / closes[MIN_HISTORY_FOR_IV] * 100, 2)
    return_on_capital = round((total_pnl / initial_capital) * 100, 2) if initial_capital else 0.0

    return BacktestResult(
        cycles=cycles,
        total_premium=total_premium,
        total_pnl=total_pnl,
        win_rate=win_rate,
        num_trades=len(cycles),
        assignments=assignments,
        max_drawdown_pct=round(max_drawdown, 2),
        return_on_capital_pct=return_on_capital,
        buy_and_hold_return_pct=buy_and_hold_pct,
        monthly_pnl={k: monthly_pnl[k] for k in sorted(monthly_pnl)},
        best_cycle_pnl=max((c.pnl for c in cycles), default=None),
        worst_cycle_pnl=min((c.pnl for c in cycles), default=None),
    )
