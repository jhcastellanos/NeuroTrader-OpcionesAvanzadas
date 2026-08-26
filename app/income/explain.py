"""IA explicativa por plantillas: parafrasea el resultado del motor, no recalcula."""
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from .schemas import (
    CompareStrategiesRequest,
    CompareStrategiesResponse,
    CoveredCallRequest,
    CashSecuredPutRequest,
    StrategyAnalysisResponse,
    StrategyExplanationResponse,
    StrategyStatus,
    StrategyType,
)
from .service import build_covered_call, build_csp
from .market import load_market

NY_TZ = ZoneInfo("America/New_York")
_FORBIDDEN_WORDS = ("garantizado", "garantiza", "seguro", "sin riesgo")
_STRATEGY_LABEL = {
    "covered_call": "Covered Call",
    "cash_secured_put": "Cash-Secured Put",
    StrategyType.COVERED_CALL: "Covered Call",
    StrategyType.CASH_SECURED_PUT: "Cash-Secured Put",
}
_STATUS_LABEL = {
    "oportunidad_valida": "oportunidad valida con riesgo controlado",
    "aceptable_con_precaucion": "oportunidad aceptable, pero con precauciones",
    "esperar": "conviene esperar antes de abrir la estrategia",
    "no_aplica": "la estrategia no aplica con los datos indicados",
    StrategyStatus.OPORTUNIDAD_VALIDA: "oportunidad valida con riesgo controlado",
    StrategyStatus.ACEPTABLE_CON_PRECAUCION: "oportunidad aceptable, pero con precauciones",
    StrategyStatus.ESPERAR: "conviene esperar antes de abrir la estrategia",
    StrategyStatus.NO_APLICA: "la estrategia no aplica con los datos indicados",
}
_AVAILABLE_STATUSES = {
    StrategyStatus.OPORTUNIDAD_VALIDA,
    StrategyStatus.ACEPTABLE_CON_PRECAUCION,
    "oportunidad_valida",
    "aceptable_con_precaucion",
}


def _status_value(status):
    return getattr(status, "value", status)


def _strategy_value(strategy):
    return getattr(strategy, "value", strategy)


class TemplateAIProvider:
    name = "template"

    async def explain_strategy(self, context):
        # type: (Dict[str, Any]) -> str
        parts = []
        ticker = context["ticker"]
        strategy_label = context["strategy_label"]
        status_label = context["status_label"]
        conviction = context["conviction_score"]
        regime = context["regime"]
        parts.append(
            "Para %s, con un regimen tecnico %s, el analisis de %s arroja el siguiente "
            "veredicto: %s, con una conviccion de %s sobre 100."
            % (ticker, regime, strategy_label, status_label, conviction)
        )
        reasons_for = context.get("reasons_for") or []
        if reasons_for:
            parts.append("A favor: " + " ".join(reasons_for[:2]))
        reasons_against = context.get("reasons_against") or []
        if reasons_against:
            parts.append("En contra: " + " ".join(reasons_against[:2]))
        balanced = context.get("balanced_contract")
        if balanced:
            metric_bits = []
            if balanced.get("strike") is not None:
                metric_bits.append("strike $%s" % balanced["strike"])
            if balanced.get("dte") is not None:
                metric_bits.append("%s dias al vencimiento" % balanced["dte"])
            if balanced.get("premium") is not None:
                metric_bits.append("prima aproximada de $%s" % balanced["premium"])
            if balanced.get("break_even") is not None:
                metric_bits.append("break-even cercano a $%s" % balanced["break_even"])
            if metric_bits:
                parts.append(
                    "El contrato con mejor equilibrio identificado por el motor tiene "
                    + ", ".join(metric_bits)
                    + "."
                )
        risks = context.get("risks") or []
        if risks:
            parts.append("Riesgo principal a vigilar: " + risks[0])
        next_event = context.get("next_important_event")
        if next_event:
            parts.append(next_event + ".")
        text = " ".join(parts)
        self._assert_no_forbidden_language(text)
        disclaimer = (
            " Esta explicacion resume datos ya calculados por el motor cuantitativo; "
            "no es asesoramiento financiero personalizado ni garantiza resultados."
        )
        return text + disclaimer

    async def compare_strategies(self, context):
        # type: (Dict[str, Any]) -> str
        ticker = context["ticker"]
        regime = context["regime"]
        cc = context.get("covered_call")
        csp = context.get("csp")
        recommended = context.get("recommended_strategy")
        reason = context.get("recommendation_reason", "")
        parts = [
            "Para %s, con un regimen tecnico %s, esto es lo que encontro el motor en ambas estrategias:"
            % (ticker, regime)
        ]
        parts.append(self._describe_side("Covered Call", cc))
        parts.append(self._describe_side("Cash-Secured Put", csp))
        if recommended:
            label = "Covered Call" if _strategy_value(recommended) == "covered_call" else "Cash-Secured Put"
            parts.append(
                "Segun la puntuacion del motor, %s tiene actualmente la mejor relacion riesgo-beneficio. %s"
                % (label, reason)
            )
        else:
            parts.append(
                "Ninguna de las dos alcanza una puntuacion solida en este momento. %s" % reason
            )
        text = " ".join(p for p in parts if p)
        self._assert_no_forbidden_language(text)
        disclaimer = (
            " Esta comparacion resume datos ya calculados por el motor cuantitativo para ambas "
            "estrategias; no es asesoramiento financiero personalizado ni garantiza resultados."
        )
        return text + disclaimer

    @staticmethod
    def _describe_side(label, side):
        # type: (str, Optional[Dict[str, Any]]) -> str
        if side is None:
            return "%s: no se evaluo (faltan datos del usuario para esta estrategia)." % label
        if not side.get("available"):
            return "%s: %s." % (label, side.get("status_label", "sin oportunidad clara"))
        bits = [
            "%s: %s, conviccion %s/100"
            % (label, side.get("status_label"), side.get("conviction_score"))
        ]
        contract = side.get("balanced_contract")
        if contract:
            metric_bits = []
            if contract.get("strike") is not None:
                metric_bits.append("strike $%s" % contract["strike"])
            if contract.get("dte") is not None:
                metric_bits.append("%s DTE" % contract["dte"])
            if contract.get("premium") is not None:
                metric_bits.append("prima $%s" % contract["premium"])
            if metric_bits:
                bits.append("(" + ", ".join(metric_bits) + ")")
        return " ".join(bits) + "."

    @staticmethod
    def _assert_no_forbidden_language(text):
        lowered = text.lower()
        for word in _FORBIDDEN_WORDS:
            if word in lowered:
                raise ValueError("Guardrail violado: la plantilla genero la palabra prohibida '%s'." % word)


def build_explain_context(result):
    # type: (StrategyAnalysisResponse) -> Dict[str, Any]
    balanced_contract = None
    if result.balanced:
        c = result.balanced.contract
        balanced_contract = {
            "strike": c.strike,
            "dte": c.dte,
            "premium": result.balanced.metrics.get("premium"),
            "break_even": result.balanced.metrics.get("break_even"),
        }
    return {
        "ticker": result.ticker,
        "strategy_label": _STRATEGY_LABEL.get(result.strategy, str(result.strategy)),
        "status_label": _STATUS_LABEL.get(result.status, str(result.status)),
        "conviction_score": result.conviction_score,
        "regime": getattr(result.regime, "value", result.regime),
        "reasons_for": result.reasons_for,
        "reasons_against": result.reasons_against,
        "risks": result.risks,
        "next_important_event": result.next_important_event,
        "balanced_contract": balanced_contract,
    }


def _build_side_context(result):
    # type: (Optional[StrategyAnalysisResponse]) -> Optional[Dict[str, Any]]
    if result is None:
        return None
    ctx = build_explain_context(result)
    ctx["available"] = result.status in _AVAILABLE_STATUSES
    return ctx


def recommend(cc, csp):
    # type: (Optional[StrategyAnalysisResponse], Optional[StrategyAnalysisResponse]) -> tuple
    cc_ok = cc is not None and cc.status in _AVAILABLE_STATUSES
    csp_ok = csp is not None and csp.status in _AVAILABLE_STATUSES
    if cc_ok and csp_ok:
        if cc.conviction_score == csp.conviction_score:
            return None, (
                "Covered Call y Cash-Secured Put empataron en conviccion "
                "(%s/100): la eleccion depende de tu preferencia "
                "(quedarte con las acciones vs. adquirirlas a descuento)."
                % cc.conviction_score
            )
        winner = (
            StrategyType.COVERED_CALL.value
            if cc.conviction_score > csp.conviction_score
            else StrategyType.CASH_SECURED_PUT.value
        )
        return winner, (
            "Covered Call obtuvo %s/100 y Cash-Secured Put %s/100 en el Option Opportunity Score "
            "del contrato equilibrado." % (cc.conviction_score, csp.conviction_score)
        )
    if cc_ok:
        return (
            StrategyType.COVERED_CALL.value,
            "Es la unica de las dos que alcanzo un estado favorable con los datos dados.",
        )
    if csp_ok:
        return (
            StrategyType.CASH_SECURED_PUT.value,
            "Es la unica de las dos que alcanzo un estado favorable con los datos dados.",
        )
    return None, "Ninguna de las dos estrategias evaluadas alcanzo un estado favorable en este momento."


async def explanation_from_result(result):
    # type: (StrategyAnalysisResponse) -> StrategyExplanationResponse
    provider = TemplateAIProvider()
    explanation = await provider.explain_strategy(build_explain_context(result))
    now_ny = datetime.now(NY_TZ)
    return StrategyExplanationResponse(
        ticker=result.ticker,
        strategy=result.strategy,
        explanation=explanation,
        provider=provider.name,
        is_demo=True,
        generated_at_ny=now_ny.strftime("%Y-%m-%d %H:%M:%S %Z"),
    )


async def compare_from_snapshot(snapshot, body):
    # type: (Any, CompareStrategiesRequest) -> CompareStrategiesResponse
    ticker = snapshot.quote.ticker
    cc_result = None
    csp_result = None
    if body.shares_owned is not None and body.cost_basis is not None:
        cc_body = CoveredCallRequest(
            ticker=ticker,
            shares_owned=body.shares_owned,
            cost_basis=body.cost_basis,
            risk_profile=body.risk_profile,
            horizon=body.horizon,
            min_yield_pct=body.min_yield_pct,
            willing_to_sell_shares=body.willing_to_sell_shares,
            max_contracts=body.max_contracts_cc,
            accept_earnings_before_expiration=body.accept_earnings_before_expiration,
        )
        cc_result = build_covered_call(snapshot, cc_body)
    if body.capital_available is not None:
        csp_body = CashSecuredPutRequest(
            ticker=ticker,
            capital_available=body.capital_available,
            risk_profile=body.risk_profile,
            horizon=body.horizon,
            min_yield_pct=body.min_yield_pct,
            willing_to_buy_shares=body.willing_to_buy_shares,
            max_contracts=body.max_contracts_csp,
            accept_earnings_before_expiration=body.accept_earnings_before_expiration,
        )
        csp_result = build_csp(snapshot, csp_body)
    if cc_result is None and csp_result is None:
        raise ValueError(
            "Da datos de al menos una estrategia: shares_owned+cost_basis para "
            "Covered Call, o capital_available para Cash-Secured Put."
        )
    recommended, reason = recommend(cc_result, csp_result)
    regime = (cc_result or csp_result).regime
    provider = TemplateAIProvider()
    ai_comparison = await provider.compare_strategies({
        "ticker": ticker,
        "regime": getattr(regime, "value", regime),
        "covered_call": _build_side_context(cc_result),
        "csp": _build_side_context(csp_result),
        "recommended_strategy": recommended,
        "recommendation_reason": reason,
    })
    now_ny = datetime.now(NY_TZ)
    is_demo = cc_result.is_demo if cc_result else csp_result.is_demo
    return CompareStrategiesResponse(
        ticker=ticker,
        regime=regime,
        covered_call=cc_result,
        csp=csp_result,
        recommended_strategy=recommended,
        recommendation_reason=reason,
        ai_comparison=ai_comparison,
        ai_provider=provider.name,
        is_demo=is_demo,
        updated_at_ny=now_ny.strftime("%Y-%m-%d %H:%M:%S %Z"),
    )


async def evaluate_covered_call(body):
    snapshot = await load_market(body.ticker)
    return build_covered_call(snapshot, body)


async def evaluate_csp(body):
    snapshot = await load_market(body.ticker)
    return build_csp(snapshot, body)


async def explain_covered_call(body):
    result = await evaluate_covered_call(body)
    return await explanation_from_result(result)


async def explain_csp(body):
    result = await evaluate_csp(body)
    return await explanation_from_result(result)


async def compare_strategies(body):
    snapshot = await load_market(body.ticker)
    return await compare_from_snapshot(snapshot, body)
