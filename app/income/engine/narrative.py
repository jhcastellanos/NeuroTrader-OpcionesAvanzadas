from typing import Optional
"""
Construye el texto explicativo (resumen, razones a favor/en contra,
riesgos, condiciones de invalidacion) a partir de datos YA calculados.
Nunca inventa cifras: todo texto aqui deriva de campos que el motor
cuantitativo ya produjo (breakdown.reasons, warnings, regime, etc.).
Esta es la unica capa "explicativa" de la Fase 3; la IA conversacional
(Fase 4) podra parafrasear esto pero no reemplazarlo.
"""
from app.income.schemas import CorporateEventsResponse, MarketRegime, StrategyStatus

_STATUS_TEXT = {
    StrategyStatus.OPORTUNIDAD_VALIDA: "una oportunidad valida con riesgo controlado",
    StrategyStatus.ACEPTABLE_CON_PRECAUCION: "una oportunidad aceptable, pero con precauciones importantes",
    StrategyStatus.ESPERAR: "que conviene esperar antes de abrir esta estrategia",
    StrategyStatus.NO_APLICA: "que esta estrategia no aplica con los datos indicados",
}


def next_event_text(events: Optional[CorporateEventsResponse]) -> Optional[str]:
    if not events:
        return None
    if events.next_earnings_date:
        suffix = " (simulado, modo demo)" if events.is_demo else ""
        return f"Proximo earnings: {events.next_earnings_date.isoformat()}{suffix}"
    if events.dividends:
        nxt = min(events.dividends, key=lambda d: d.ex_dividend_date)
        return f"Proximo ex-dividendo: {nxt.ex_dividend_date.isoformat()} (${nxt.cash_amount})"
    return None


def build_summary(ticker: str, regime: MarketRegime, status: StrategyStatus, best_score: Optional[int]) -> str:
    status_text = _STATUS_TEXT[status]
    if best_score is not None:
        return (
            f"{ticker} muestra un regimen tecnico {regime}. El analisis indica {status_text}. "
            f"El mejor contrato equilibrado obtuvo {best_score}/100 en el Option Opportunity Score."
        )
    return f"{ticker} muestra un regimen tecnico {regime}, pero {status_text}."


def build_covered_call_narrative(
    *, request, regime: MarketRegime, balanced_candidate, has_earnings_before_expiration: bool
) -> tuple[list[str], list[str], list[str], list[str]]:
    reasons_for: list[str] = []
    reasons_against: list[str] = []
    risks: list[str] = [
        "Si el precio sube con fuerza, el upside queda limitado por el strike vendido.",
        "Las acciones subyacentes pueden perder valor mientras se mantiene la posicion cubierta.",
    ]
    invalidation: list[str] = [
        "El precio rompe con fuerza la resistencia tecnica usada como referencia del strike.",
        "Aparece un evento corporativo relevante no contemplado en este analisis.",
    ]

    if regime in (MarketRegime.LATERAL, MarketRegime.ALCISTA):
        reasons_for.append(f"El regimen tecnico actual ('{regime}') es compatible con vender Covered Calls.")
    else:
        reasons_against.append(f"El regimen tecnico actual ('{regime}') no es el ideal para Covered Calls.")

    if balanced_candidate:
        reasons_for.extend(balanced_candidate.breakdown.reasons[:3])
        reasons_against.extend(balanced_candidate.warnings)
    else:
        reasons_against.append("No se encontro ningun contrato que cumpla los filtros minimos de liquidez y vencimiento.")

    if has_earnings_before_expiration and not getattr(request, "accept_earnings_before_expiration", False):
        risks.append("Hay earnings antes del vencimiento: la volatilidad puede moverse bruscamente en cualquier direccion.")

    return reasons_for, reasons_against, risks, invalidation


def build_csp_narrative(
    *, request, regime: MarketRegime, balanced_candidate, has_earnings_before_expiration: bool
) -> tuple[list[str], list[str], list[str], list[str]]:
    reasons_for: list[str] = []
    reasons_against: list[str] = []
    risks: list[str] = [
        "Si el precio cae con fuerza por debajo del strike, se asignaran las acciones a un costo efectivo mayor al break-even actual.",
        "El capital reservado queda inmovilizado durante toda la vida del contrato.",
    ]
    invalidation: list[str] = [
        "El precio rompe con fuerza el soporte tecnico usado como referencia del strike.",
        "Aparece un evento corporativo relevante no contemplado en este analisis.",
    ]

    if regime != MarketRegime.ALTA_VOLATILIDAD:
        reasons_for.append(f"El regimen tecnico actual ('{regime}') no muestra volatilidad extrema desfavorable.")
    else:
        reasons_against.append("El regimen actual es de alta volatilidad: el riesgo de un movimiento brusco es mayor.")

    if balanced_candidate:
        reasons_for.extend(balanced_candidate.breakdown.reasons[:3])
        reasons_against.extend(balanced_candidate.warnings)
    else:
        reasons_against.append("No se encontro ningun contrato que cumpla los filtros minimos de liquidez y vencimiento.")

    if has_earnings_before_expiration and not getattr(request, "accept_earnings_before_expiration", False):
        risks.append("Hay earnings antes del vencimiento: la volatilidad puede moverse bruscamente en cualquier direccion.")

    return reasons_for, reasons_against, risks, invalidation
