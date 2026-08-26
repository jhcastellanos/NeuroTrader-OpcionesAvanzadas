from typing import Optional
"""
Motor de Covered Call — formulas de la seccion 7 y logica de seleccion
de contratos / veredicto de estrategia de la seccion 11 del brief.
"""
from dataclasses import dataclass
from datetime import date

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
    ContractEvaluation,
    ContractRole,
    ContractScoreBreakdown,
    CoveredCallRequest,
    DividendEvent,
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
class CoveredCallMetrics:
    premium: float
    prima_total: float
    rendimiento_prima: Optional[float]
    rendimiento_sobre_costo: Optional[float]
    ganancia_si_asignado: float
    retorno_si_asignado: Optional[float]
    break_even: float
    upside_restante: Optional[float]

    def as_dict(self) -> dict[str, Optional[float]]:
        return {
            "premium": self.premium,
            "prima_total": self.prima_total,
            "rendimiento_prima": self.rendimiento_prima,
            "rendimiento_sobre_costo": self.rendimiento_sobre_costo,
            "ganancia_si_asignado": self.ganancia_si_asignado,
            "retorno_si_asignado": self.retorno_si_asignado,
            "break_even": self.break_even,
            "upside_restante": self.upside_restante,
        }


def compute_covered_call_metrics(
    contract: OptionContract, cost_basis: float, price: float, contracts: int = 1
) -> CoveredCallMetrics:
    """Formulas exactas de la seccion 7 del brief."""
    premium = contract.mid if contract.mid is not None else (contract.last or 0.0)
    prima_total = premium * 100 * contracts
    rendimiento_prima = premium / price if price else None
    rendimiento_sobre_costo = premium / cost_basis if cost_basis else None
    ganancia_si_asignado = ((contract.strike - cost_basis) + premium) * 100 * contracts
    retorno_si_asignado = ((contract.strike - cost_basis) + premium) / cost_basis if cost_basis else None
    break_even = cost_basis - premium
    upside_restante = (contract.strike - price) / price if price else None

    return CoveredCallMetrics(
        premium=round(premium, 4),
        prima_total=round(prima_total, 2),
        rendimiento_prima=round(rendimiento_prima, 4) if rendimiento_prima is not None else None,
        rendimiento_sobre_costo=round(rendimiento_sobre_costo, 4) if rendimiento_sobre_costo is not None else None,
        ganancia_si_asignado=round(ganancia_si_asignado, 2),
        retorno_si_asignado=round(retorno_si_asignado, 4) if retorno_si_asignado is not None else None,
        break_even=round(break_even, 2),
        upside_restante=round(upside_restante, 4) if upside_restante is not None else None,
    )


def compute_extrinsic_value(contract: OptionContract, price: float) -> Optional[float]:
    """Valor extrinseco = prima - valor intrinseco. Para un call: intrinseco = max(precio-strike, 0)."""
    premium = contract.mid if contract.mid is not None else contract.last
    if premium is None:
        return None
    intrinsic = max(price - contract.strike, 0.0)
    return round(max(premium - intrinsic, 0.0), 4)


def dividend_amount_before_expiration(
    dividends: Optional[list[DividendEvent]], expiration_date: date
) -> Optional[float]:
    """
    Devuelve el monto del dividendo mas proximo cuya fecha ex-dividendo
    cae entre hoy y el vencimiento del contrato (inclusive), o None si
    no hay ninguno. Se usa para el riesgo de asignacion anticipada
    (seccion 7 del brief): un call ITM con poco valor extrinseco antes
    de una fecha ex-dividendo es un candidato real a ser ejercido
    anticipadamente por el tenedor para capturar el dividendo.
    """
    if not dividends:
        return None
    today = date.today()
    upcoming = [d for d in dividends if today <= d.ex_dividend_date <= expiration_date]
    if not upcoming:
        return None
    nearest = min(upcoming, key=lambda d: d.ex_dividend_date)
    return nearest.cash_amount


def score_technical_location(
    strike: float,
    cost_basis: float,
    resistances: list[float],
    strike_must_be_above_cost_basis: bool,
) -> ScorePart:
    max_points = 20
    reasons: list[str] = []
    points = 0

    if strike < cost_basis:
        if strike_must_be_above_cost_basis:
            reasons.append(
                f"ADVERTENCIA: strike (${strike}) por debajo del costo promedio (${cost_basis}) (+0/10)."
            )
        else:
            points += 4
            reasons.append(
                f"Strike (${strike}) debajo del costo promedio, aceptado explicitamente por el usuario (+4/10)."
            )
    else:
        cushion_pct = (strike - cost_basis) / cost_basis if cost_basis else 0
        if cushion_pct >= 0.03:
            points += 10
            reasons.append(f"Strike (${strike}) con margen comodo sobre el costo promedio ({cushion_pct*100:.1f}%) (+10/10).")
        else:
            points += 6
            reasons.append(f"Strike (${strike}) apenas por encima del costo promedio (+6/10).")

    nearby_resistance = min((r for r in resistances if r >= strike * 0.97), default=None)
    if nearby_resistance is not None:
        points += 10
        reasons.append(f"Strike cerca o sobre una resistencia tecnica (${nearby_resistance}) (+10/10).")
    elif resistances:
        points += 5
        reasons.append("Hay resistencias detectadas, pero ninguna cerca de este strike (+5/10).")
    else:
        points += 3
        reasons.append("No se detectaron resistencias tecnicas claras para este activo (+3/10).")

    return ScorePart(min(points, max_points), max_points, reasons)


def score_premium_yield(rendimiento_prima: Optional[float], min_yield_pct: float) -> ScorePart:
    max_points = 15
    if rendimiento_prima is None:
        return ScorePart(5, max_points, ["Rendimiento de prima no calculable (+5/15)."])
    yield_pct = rendimiento_prima * 100
    if yield_pct >= min_yield_pct * 1.5:
        return ScorePart(max_points, max_points, [f"Rendimiento de prima ({yield_pct:.2f}%) supera comodamente el minimo deseado ({min_yield_pct}%) (+15/15)."])
    if yield_pct >= min_yield_pct:
        return ScorePart(10, max_points, [f"Rendimiento de prima ({yield_pct:.2f}%) cumple el minimo deseado ({min_yield_pct}%) (+10/15)."])
    if yield_pct >= min_yield_pct * 0.6:
        return ScorePart(5, max_points, [f"Rendimiento de prima ({yield_pct:.2f}%) por debajo del minimo deseado (+5/15)."])
    return ScorePart(1, max_points, [f"Rendimiento de prima ({yield_pct:.2f}%) muy por debajo del minimo deseado (+1/15)."])


def evaluate_covered_call_contract(
    contract: OptionContract,
    *,
    request: CoveredCallRequest,
    price: float,
    resistances: list[float],
    has_earnings_before_expiration: bool,
    dividends: Optional[list[DividendEvent]] = None,
) -> tuple[ContractScoreBreakdown, CoveredCallMetrics, list[str]]:
    warnings: list[str] = []
    metrics = compute_covered_call_metrics(contract, request.cost_basis, price, request.max_contracts)

    if not contract.bid or contract.bid <= 0:
        warnings.append("Bid en cero o no disponible: la ejecucion real puede ser desfavorable o imposible.")
    if contract.spread_percent is not None and contract.spread_percent > 15:
        warnings.append(f"Spread muy amplio ({contract.spread_percent}%): el precio medio puede no ser ejecutable.")
    if not contract.open_interest:
        warnings.append("Open interest en cero: contrato practicamente sin liquidez.")
    if not request.willing_to_sell_shares:
        warnings.append("El usuario indico que no esta dispuesto a vender sus acciones al strike.")
    if contract.strike < request.cost_basis and request.strike_must_be_above_cost_basis:
        warnings.append("Strike por debajo del costo promedio: la asignacion implicaria vender bajo tu costo base.")
    shares_needed = request.max_contracts * 100
    if shares_needed > request.shares_owned:
        warnings.append(
            f"Necesitas {shares_needed} acciones para cubrir {request.max_contracts} contrato(s), "
            f"pero indicaste poseer {request.shares_owned}. Estos contratos NO estarian completamente "
            f"cubiertos (parte quedaria como calls al descubierto, con riesgo distinto e ilimitado)."
        )
    if has_earnings_before_expiration and not request.accept_earnings_before_expiration:
        warnings.append("Hay earnings antes del vencimiento y no fue aceptado explicitamente.")

    div_amount = dividend_amount_before_expiration(dividends, contract.expiration_date)
    extrinsic = compute_extrinsic_value(contract, price)
    is_itm_now = price > contract.strike
    has_dividend_assignment_risk = (
        div_amount is not None and is_itm_now and extrinsic is not None and extrinsic < div_amount
    )
    if has_dividend_assignment_risk:
        warnings.append(
            f"Riesgo de asignacion anticipada: hay un dividendo de ${div_amount} antes del vencimiento "
            f"y el valor extrinseco actual (${extrinsic}) es menor a ese monto — al tenedor del call le "
            f"conviene ejercer antes para capturar el dividendo."
        )
    if request.min_profit_if_assigned is not None and metrics.ganancia_si_asignado < request.min_profit_if_assigned:
        warnings.append(
            f"La ganancia si es asignado (${metrics.ganancia_si_asignado}) es menor a tu minimo deseado "
            f"(${request.min_profit_if_assigned})."
        )

    target_low, target_high = DELTA_BANDS[request.risk_profile]

    liquidity = score_liquidity(contract.open_interest, contract.volume, contract.spread_percent)
    location = score_technical_location(contract.strike, request.cost_basis, resistances, request.strike_must_be_above_cost_basis)
    premium_yield = score_premium_yield(metrics.rendimiento_prima, request.min_yield_pct)
    delta_score = score_delta_band(contract.delta, target_low, target_high)
    iv_pct = contract.implied_volatility * 100 if contract.implied_volatility is not None else None
    vol_score = score_volatility_component(iv_pct)
    dte_score = score_dte(contract.dte)
    event_score = score_event_risk(has_earnings_before_expiration, request.accept_earnings_before_expiration)

    total = (
        liquidity.points + location.points + premium_yield.points
        + delta_score.points + vol_score.points + dte_score.points + event_score.points
    )

    # Penalizaciones severas explicitas (seccion 10 del brief)
    if contract.strike < request.cost_basis and request.strike_must_be_above_cost_basis:
        total = max(0, total - 25)
    if request.max_contracts * 100 > request.shares_owned:
        total = max(0, total - 40)  # nunca debe verse como una buena opcion sin acciones suficientes
    if has_dividend_assignment_risk:
        total = max(0, total - 10)
    if not request.willing_to_sell_shares:
        total = max(0, total - 20)
    if contract.spread_percent is not None and contract.spread_percent > 20:
        total = max(0, total - 15)
    if has_earnings_before_expiration and not request.accept_earnings_before_expiration:
        total = max(0, total - 10)
    total = min(total, 100)

    reasons = (
        liquidity.reasons + location.reasons + premium_yield.reasons
        + delta_score.reasons + vol_score.reasons + dte_score.reasons + event_score.reasons
    )

    breakdown = ContractScoreBreakdown(
        liquidity_score=liquidity.points,
        technical_location_score=location.points,
        premium_yield_score=premium_yield.points,
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
    metrics: CoveredCallMetrics
    warnings: list[str]


def select_covered_call_candidates(
    chain_contracts: list[OptionContract],
    *,
    request: CoveredCallRequest,
    price: float,
    resistances: list[float],
    has_earnings_before_expiration: bool,
    dividends: Optional[list[DividendEvent]] = None,
    min_dte: int = 5,
    max_dte: int = 60,
) -> list[ScoredCandidate]:
    calls = [
        c for c in chain_contracts
        if c.contract_type == "call" and min_dte <= c.dte <= max_dte
    ]
    scored: list[ScoredCandidate] = []
    for contract in calls:
        breakdown, metrics, warnings = evaluate_covered_call_contract(
            contract,
            request=request,
            price=price,
            resistances=resistances,
            has_earnings_before_expiration=has_earnings_before_expiration,
            dividends=dividends,
        )
        scored.append(ScoredCandidate(contract, breakdown, metrics, warnings))
    return scored


_AGGRESSIVE_DELTA_CEILING = 0.50  # techo razonable: mas alla de esto ya no es "vender prima", es casi ITM
_CONSERVATIVE_DELTA_FLOOR = 0.05  # piso razonable: por debajo de esto la prima suele ser insignificante


def pick_three_roles(
    scored: list[ScoredCandidate],
) -> dict[str, Optional[ScoredCandidate]]:
    """
    Conservador = menor |delta| DENTRO de un piso razonable
    (`_CONSERVATIVE_DELTA_FLOOR`) — sin este piso, un contrato tan
    profundamente OTM que su prima es practicamente insignificante
    podia aparecer como "conservador" solo por tener el |delta| mas
    bajo de toda la cadena. Agresivo = mayor |delta| DENTRO de un techo
    razonable (`_AGGRESSIVE_DELTA_CEILING`) — sin este techo, un
    contrato profundamente ITM (delta cercano a ±1, casi asignacion
    segura) podia aparecer como "agresivo" solo por tener el |delta|
    mas alto de toda la cadena, aunque no sea una venta de prima
    genuina en el sentido que pide la seccion 7 del brief (delta
    objetivo agresivo: 0.30-0.40; conservador: 0.10-0.20).
    Equilibrado = mejor score total. Solo se consideran candidatos con
    delta conocido para conservador/agresivo; si no hay ninguno valido,
    el rol queda en None (nunca se inventa un contrato).
    """
    with_delta = [s for s in scored if s.contract.delta is not None]
    if not scored:
        return {"conservative": None, "balanced": None, "aggressive": None}

    balanced = max(scored, key=lambda s: s.breakdown.total_score)
    if with_delta:
        above_floor = [s for s in with_delta if abs(s.contract.delta) >= _CONSERVATIVE_DELTA_FLOOR]
        conservative_pool = above_floor if above_floor else with_delta
        conservative = min(conservative_pool, key=lambda s: abs(s.contract.delta))

        within_ceiling = [s for s in with_delta if abs(s.contract.delta) <= _AGGRESSIVE_DELTA_CEILING]
        aggressive_pool = within_ceiling if within_ceiling else with_delta
        aggressive = max(aggressive_pool, key=lambda s: abs(s.contract.delta))
    else:
        conservative = min(scored, key=lambda s: s.contract.strike, default=None)
        aggressive = max(scored, key=lambda s: s.contract.strike, default=None)

    return {"conservative": conservative, "balanced": balanced, "aggressive": aggressive}


def determine_status(best_score: Optional[int]) -> StrategyStatus:
    if best_score is None:
        return StrategyStatus.NO_APLICA
    if best_score >= 65:
        return StrategyStatus.OPORTUNIDAD_VALIDA
    if best_score >= 50:
        return StrategyStatus.ACEPTABLE_CON_PRECAUCION
    return StrategyStatus.ESPERAR
