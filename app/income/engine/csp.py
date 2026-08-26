from typing import Optional
"""
Motor de Cash-Secured Put — formulas de la seccion 8 y logica de
seleccion de contratos de la seccion 11 del brief.
"""
from dataclasses import dataclass

from app.income.engine.contract_score import (
    ScorePart,
    classify_total_score,
    score_delta_band,
    score_dte,
    score_event_risk,
    score_liquidity,
    score_volatility_component,
)
from app.income.schemas import (
    CashSecuredPutRequest,
    ContractScoreBreakdown,
    OptionContract,
    RiskProfile,
    StrategyStatus,
)

DELTA_BANDS: dict[str, tuple[float, float]] = {
    RiskProfile.CONSERVADOR.value: (0.10, 0.20),
    RiskProfile.EQUILIBRADO.value: (0.20, 0.30),
    RiskProfile.AGRESIVO.value: (0.30, 0.40),
}


@dataclass
class CSPMetrics:
    premium: float
    capital_requerido: float
    prima_total: float
    break_even: float
    descuento_efectivo: Optional[float]
    retorno_sobre_capital: Optional[float]
    retorno_anualizado_aprox: Optional[float]

    def as_dict(self) -> dict[str, Optional[float]]:
        return {
            "premium": self.premium,
            "capital_requerido": self.capital_requerido,
            "prima_total": self.prima_total,
            "break_even": self.break_even,
            "descuento_efectivo": self.descuento_efectivo,
            "retorno_sobre_capital": self.retorno_sobre_capital,
            "retorno_anualizado_aprox": self.retorno_anualizado_aprox,
        }


def compute_csp_metrics(contract: OptionContract, price: float, contracts: int = 1) -> CSPMetrics:
    """Formulas exactas de la seccion 8 del brief."""
    premium = contract.mid if contract.mid is not None else (contract.last or 0.0)
    capital_requerido = contract.strike * 100 * contracts
    prima_total = premium * 100 * contracts
    break_even = contract.strike - premium
    descuento_efectivo = (price - break_even) / price if price else None
    retorno_sobre_capital = premium / contract.strike if contract.strike else None
    retorno_anualizado = (
        (premium / contract.strike) * (365 / contract.dte)
        if contract.strike and contract.dte > 0
        else None
    )

    return CSPMetrics(
        premium=round(premium, 4),
        capital_requerido=round(capital_requerido, 2),
        prima_total=round(prima_total, 2),
        break_even=round(break_even, 2),
        descuento_efectivo=round(descuento_efectivo, 4) if descuento_efectivo is not None else None,
        retorno_sobre_capital=round(retorno_sobre_capital, 4) if retorno_sobre_capital is not None else None,
        retorno_anualizado_aprox=round(retorno_anualizado, 4) if retorno_anualizado is not None else None,
    )


def score_technical_location(strike: float, supports: list[float]) -> ScorePart:
    max_points = 20
    reasons: list[str] = []
    points = 0

    nearby_support = max((s for s in supports if s <= strike * 1.03), default=None)
    if nearby_support is not None:
        points += 14
        reasons.append(f"Strike cerca o debajo de un soporte tecnico (${nearby_support}) (+14/14).")
    elif supports:
        points += 6
        reasons.append("Hay soportes detectados, pero ninguno cerca de este strike (+6/14).")
    else:
        points += 4
        reasons.append("No se detectaron soportes tecnicos claros para este activo (+4/14).")

    if supports:
        weakest_support = min(supports)
        if strike < weakest_support:
            points += 6
            reasons.append(f"Strike (${strike}) por debajo incluso del soporte mas debil (${weakest_support}) (+6/6).")
        else:
            points += 3
            reasons.append("Strike por encima del soporte mas debil detectado (+3/6).")
    else:
        points += 3
        reasons.append("Sin soportes para comparar la robustez del strike (+3/6).")

    return ScorePart(min(points, max_points), max_points, reasons)


def score_capital_efficiency(retorno_sobre_capital: Optional[float], min_yield_pct: float) -> ScorePart:
    max_points = 15
    if retorno_sobre_capital is None:
        return ScorePart(5, max_points, ["Retorno sobre capital no calculable (+5/15)."])
    yield_pct = retorno_sobre_capital * 100
    if yield_pct >= min_yield_pct * 1.5:
        return ScorePart(max_points, max_points, [f"Retorno sobre capital ({yield_pct:.2f}%) supera comodamente el minimo deseado ({min_yield_pct}%) (+15/15)."])
    if yield_pct >= min_yield_pct:
        return ScorePart(10, max_points, [f"Retorno sobre capital ({yield_pct:.2f}%) cumple el minimo deseado ({min_yield_pct}%) (+10/15)."])
    if yield_pct >= min_yield_pct * 0.6:
        return ScorePart(5, max_points, [f"Retorno sobre capital ({yield_pct:.2f}%) por debajo del minimo deseado (+5/15)."])
    return ScorePart(1, max_points, [f"Retorno sobre capital ({yield_pct:.2f}%) muy por debajo del minimo deseado (+1/15)."])


def evaluate_csp_contract(
    contract: OptionContract,
    *,
    request: CashSecuredPutRequest,
    price: float,
    supports: list[float],
    has_earnings_before_expiration: bool,
) -> tuple[ContractScoreBreakdown, CSPMetrics, list[str]]:
    warnings: list[str] = []
    metrics = compute_csp_metrics(contract, price, request.max_contracts)

    if not contract.bid or contract.bid <= 0:
        warnings.append("Bid en cero o no disponible: la ejecucion real puede ser desfavorable o imposible.")
    if contract.spread_percent is not None and contract.spread_percent > 15:
        warnings.append(f"Spread muy amplio ({contract.spread_percent}%): el precio medio puede no ser ejecutable.")
    if not contract.open_interest:
        warnings.append("Open interest en cero: contrato practicamente sin liquidez.")
    if metrics.capital_requerido > request.capital_available:
        warnings.append(
            f"Capital requerido (${metrics.capital_requerido}) supera el capital disponible "
            f"(${request.capital_available}): este contrato NO puede presentarse como cash-secured."
        )
    if not request.willing_to_buy_shares:
        warnings.append("El usuario indico que no desea realmente poseer las acciones subyacentes.")
    if request.max_effective_price is not None and metrics.break_even > request.max_effective_price:
        warnings.append(
            f"El break-even (${metrics.break_even}) supera el precio efectivo maximo deseado "
            f"(${request.max_effective_price})."
        )
    if request.max_portfolio_pct is not None and request.capital_available > 0:
        pct_of_capital = metrics.capital_requerido / request.capital_available * 100
        if pct_of_capital > request.max_portfolio_pct:
            warnings.append(
                f"El capital requerido representa {pct_of_capital:.1f}% del capital disponible, "
                f"por encima del limite de {request.max_portfolio_pct}%."
            )
    if has_earnings_before_expiration and not request.accept_earnings_before_expiration:
        warnings.append("Hay earnings antes del vencimiento y no fue aceptado explicitamente.")

    target_low, target_high = DELTA_BANDS[request.risk_profile]

    liquidity = score_liquidity(contract.open_interest, contract.volume, contract.spread_percent)
    location = score_technical_location(contract.strike, supports)
    capital_eff = score_capital_efficiency(metrics.retorno_sobre_capital, request.min_yield_pct)
    delta_score = score_delta_band(contract.delta, target_low, target_high)
    iv_pct = contract.implied_volatility * 100 if contract.implied_volatility is not None else None
    vol_score = score_volatility_component(iv_pct)
    dte_score = score_dte(contract.dte)
    event_score = score_event_risk(has_earnings_before_expiration, request.accept_earnings_before_expiration)

    total = (
        liquidity.points + location.points + capital_eff.points
        + delta_score.points + vol_score.points + dte_score.points + event_score.points
    )

    # Penalizaciones severas explicitas (seccion 8 y 10 del brief)
    if metrics.capital_requerido > request.capital_available:
        total = max(0, total - 40)  # nunca debe verse como una buena opcion sin el capital
    if not request.willing_to_buy_shares:
        total = max(0, total - 20)
    if contract.spread_percent is not None and contract.spread_percent > 20:
        total = max(0, total - 15)
    if has_earnings_before_expiration and not request.accept_earnings_before_expiration:
        total = max(0, total - 10)
    total = min(total, 100)

    reasons = (
        liquidity.reasons + location.reasons + capital_eff.reasons
        + delta_score.reasons + vol_score.reasons + dte_score.reasons + event_score.reasons
    )

    breakdown = ContractScoreBreakdown(
        liquidity_score=liquidity.points,
        technical_location_score=location.points,
        premium_yield_score=capital_eff.points,
        delta_probability_score=delta_score.points,
        volatility_score=vol_score.points,
        dte_score=dte_score.points,
        event_risk_score=event_score.points,
        total_score=total,
        classification=classify_total_score(total),
        reasons=reasons,
    )
    return breakdown, metrics, warnings


@dataclass
class ScoredCandidate:
    contract: OptionContract
    breakdown: ContractScoreBreakdown
    metrics: CSPMetrics
    warnings: list[str]


def select_csp_candidates(
    chain_contracts: list[OptionContract],
    *,
    request: CashSecuredPutRequest,
    price: float,
    supports: list[float],
    has_earnings_before_expiration: bool,
    min_dte: int = 5,
    max_dte: int = 60,
) -> list[ScoredCandidate]:
    puts = [
        c for c in chain_contracts
        if c.contract_type == "put" and min_dte <= c.dte <= max_dte
    ]
    scored: list[ScoredCandidate] = []
    for contract in puts:
        breakdown, metrics, warnings = evaluate_csp_contract(
            contract,
            request=request,
            price=price,
            supports=supports,
            has_earnings_before_expiration=has_earnings_before_expiration,
        )
        scored.append(ScoredCandidate(contract, breakdown, metrics, warnings))
    return scored


_AGGRESSIVE_DELTA_CEILING = 0.50  # techo razonable: mas alla de esto ya no es "vender prima", es casi ITM
_CONSERVATIVE_DELTA_FLOOR = 0.05  # piso razonable: por debajo de esto la prima suele ser insignificante


def pick_three_roles(scored: list[ScoredCandidate]) -> dict[str, Optional[ScoredCandidate]]:
    """
    Igual criterio que en Covered Call: conservador = menor |delta|
    dentro de un piso razonable (`_CONSERVATIVE_DELTA_FLOOR`), agresivo
    = mayor |delta| dentro de un techo razonable
    (`_AGGRESSIVE_DELTA_CEILING`). Sin estos limites, un put
    profundamente OTM (prima insignificante) o profundamente ITM (casi
    asignacion segura) podia surgir como "conservador" o "agresivo"
    solo por tener el |delta| extremo de toda la cadena — la seccion 8
    del brief define el delta objetivo conservador como 0.10-0.20 y el
    agresivo como 0.30-0.40, no extremos sin limite.
    """
    if not scored:
        return {"conservative": None, "balanced": None, "aggressive": None}

    with_delta = [s for s in scored if s.contract.delta is not None]
    balanced = max(scored, key=lambda s: s.breakdown.total_score)
    if with_delta:
        above_floor = [s for s in with_delta if abs(s.contract.delta) >= _CONSERVATIVE_DELTA_FLOOR]
        conservative_pool = above_floor if above_floor else with_delta
        conservative = min(conservative_pool, key=lambda s: abs(s.contract.delta))

        within_ceiling = [s for s in with_delta if abs(s.contract.delta) <= _AGGRESSIVE_DELTA_CEILING]
        aggressive_pool = within_ceiling if within_ceiling else with_delta
        aggressive = max(aggressive_pool, key=lambda s: abs(s.contract.delta))
    else:
        conservative = max(scored, key=lambda s: s.contract.strike, default=None)
        aggressive = min(scored, key=lambda s: s.contract.strike, default=None)

    return {"conservative": conservative, "balanced": balanced, "aggressive": aggressive}


def determine_status(best_score: Optional[int]) -> StrategyStatus:
    if best_score is None:
        return StrategyStatus.NO_APLICA
    if best_score >= 65:
        return StrategyStatus.OPORTUNIDAD_VALIDA
    if best_score >= 50:
        return StrategyStatus.ACEPTABLE_CON_PRECAUCION
    return StrategyStatus.ESPERAR
