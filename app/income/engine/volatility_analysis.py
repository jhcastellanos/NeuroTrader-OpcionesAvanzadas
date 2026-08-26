from typing import Optional
"""Compone VolatilityAnalysis a partir de una cadena de opciones real/demo."""
from app.income.engine.volatility import (
    IV_RANK_LIMITATION_NOTE,
    SkewInput,
    expected_move,
    interpret_volatility,
    put_call_skew_pct,
)
from app.income.schemas import OptionChainResponse, VolatilityAnalysis


def build_volatility_analysis(
    chain: Optional[OptionChainResponse],
    price: float,
    historical_volatility_pct: Optional[float],
    has_earnings_within_dte: bool,
) -> VolatilityAnalysis:
    limitations = [IV_RANK_LIMITATION_NOTE]

    if not chain or not chain.contracts:
        return VolatilityAnalysis(
            historical_volatility_pct=historical_volatility_pct,
            interpretation=interpret_volatility(
                historical_volatility_pct=historical_volatility_pct,
                atm_iv_pct=None,
                has_earnings_within_dte=has_earnings_within_dte,
            ),
            limitations=limitations + ["Sin contratos disponibles para estimar la IV ATM."],
        )

    nearest_expiration = min(c.expiration_date for c in chain.contracts)
    same_expiration = [c for c in chain.contracts if c.expiration_date == nearest_expiration]

    atm_call = min(
        (c for c in same_expiration if c.contract_type == "call"),
        key=lambda c: abs(c.strike - price),
        default=None,
    )
    atm_put = min(
        (c for c in same_expiration if c.contract_type == "put"),
        key=lambda c: abs(c.strike - price),
        default=None,
    )

    atm_iv_pct = None
    if atm_call and atm_call.implied_volatility:
        atm_iv_pct = round(atm_call.implied_volatility * 100, 2)
    elif atm_put and atm_put.implied_volatility:
        atm_iv_pct = round(atm_put.implied_volatility * 100, 2)

    dte = same_expiration[0].dte if same_expiration else None
    move = None
    if atm_call and atm_call.implied_volatility and dte:
        move = expected_move(price, atm_call.implied_volatility, dte)

    skew = put_call_skew_pct(
        SkewInput(
            call_iv_pct=round(atm_call.implied_volatility * 100, 2) if atm_call and atm_call.implied_volatility else None,
            put_iv_pct=round(atm_put.implied_volatility * 100, 2) if atm_put and atm_put.implied_volatility else None,
        )
    )

    iv_hv_ratio = (
        round(atm_iv_pct / historical_volatility_pct, 2)
        if atm_iv_pct and historical_volatility_pct
        else None
    )

    interpretation = interpret_volatility(
        historical_volatility_pct=historical_volatility_pct,
        atm_iv_pct=atm_iv_pct,
        has_earnings_within_dte=has_earnings_within_dte,
    )

    return VolatilityAnalysis(
        historical_volatility_pct=historical_volatility_pct,
        atm_implied_volatility_pct=atm_iv_pct,
        iv_hv_ratio=iv_hv_ratio,
        iv_rank=None,
        iv_percentile=None,
        put_call_iv_skew_pct=skew,
        expected_move_dollars=move[0] if move else None,
        expected_move_pct=move[1] if move else None,
        interpretation=interpretation,
        limitations=limitations,
    )
