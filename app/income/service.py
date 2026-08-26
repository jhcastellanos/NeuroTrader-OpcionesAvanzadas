"""Orquesta el motor de Covered Call / CSP sobre el ticker seleccionado."""
from datetime import date
from typing import Optional

from .engine import covered_call as cc_engine
from .engine import csp as csp_engine
from .engine import narrative
from .engine.technical_analysis import build_technical_analysis
from .engine.volatility_analysis import build_volatility_analysis
from .market import MarketSnapshot, load_market
from .schemas import (
    CashSecuredPutRequest,
    ContractEvaluation,
    ContractRole,
    CorporateEventsResponse,
    CoveredCallRequest,
    StrategyAnalysisResponse,
    StrategyStatus,
    StrategyType,
    UnderlyingAnalysisResponse,
)

_EARNINGS_RELEVANT_WINDOW_DAYS = 45


def _has_earnings_within_window(events: Optional[CorporateEventsResponse], days: int) -> bool:
    if not events or not events.next_earnings_date:
        return False
    return (events.next_earnings_date - date.today()).days <= days


def _to_evaluation(role: ContractRole, candidate) -> Optional[ContractEvaluation]:
    if candidate is None:
        return None
    return ContractEvaluation(
        role=role,
        contract=candidate.contract,
        score=candidate.breakdown,
        metrics=candidate.metrics.as_dict(),
        warnings=candidate.warnings,
    )


def _best_balance_role(conservative, balanced, aggressive) -> Optional[ContractRole]:
    scored = {
        ContractRole.CONSERVADOR: conservative.breakdown.total_score if conservative else -1,
        ContractRole.EQUILIBRADO: balanced.breakdown.total_score if balanced else -1,
        ContractRole.AGRESIVO: aggressive.breakdown.total_score if aggressive else -1,
    }
    if all(v == -1 for v in scored.values()):
        return None
    return max(scored, key=scored.get)


def build_underlying(snapshot: MarketSnapshot) -> UnderlyingAnalysisResponse:
    technical, score = build_technical_analysis(snapshot.quote, snapshot.bars, snapshot.events)
    return UnderlyingAnalysisResponse(
        ticker=snapshot.quote.ticker,
        technical=technical,
        score=score,
        regime=technical.regime,
        data_source_status=technical.data_source_status,
        is_demo=technical.is_demo,
        updated_at_ny=technical.updated_at_ny,
    )


def build_covered_call(snapshot: MarketSnapshot, body: CoveredCallRequest) -> StrategyAnalysisResponse:
    ticker = snapshot.quote.ticker
    quote = snapshot.quote
    events = snapshot.events
    technical, _score = build_technical_analysis(quote, snapshot.bars, events)
    has_earnings = _has_earnings_within_window(events, _EARNINGS_RELEVANT_WINDOW_DAYS)
    if body.shares_owned < 100:
        volatility = build_volatility_analysis(None, quote.price, technical.historical_volatility_pct, has_earnings)
        return StrategyAnalysisResponse(
            ticker=ticker,
            strategy=StrategyType.COVERED_CALL,
            regime=technical.regime,
            status=StrategyStatus.NO_APLICA,
            conviction_score=0,
            summary=f"Covered Call requiere al menos 100 acciones por contrato; indicaste {body.shares_owned}.",
            reasons_against=["No posees las 100 acciones mínimas requeridas por contrato."],
            next_important_event=narrative.next_event_text(events),
            volatility=volatility,
            alternative_action="Espera a poseer al menos 100 acciones antes de considerar un Covered Call.",
            data_source_status=technical.data_source_status,
            is_demo=technical.is_demo or snapshot.chain.is_demo,
            updated_at_ny=technical.updated_at_ny,
        )
    candidates = cc_engine.select_covered_call_candidates(
        snapshot.chain.contracts,
        request=body,
        price=quote.price,
        resistances=technical.resistances,
        has_earnings_before_expiration=has_earnings,
        dividends=events.dividends if events else None,
    )
    roles = cc_engine.pick_three_roles(candidates)
    balanced = roles["balanced"]
    best_score = balanced.breakdown.total_score if balanced else None
    status = cc_engine.determine_status(best_score)
    volatility = build_volatility_analysis(snapshot.chain, quote.price, technical.historical_volatility_pct, has_earnings)
    reasons_for, reasons_against, risks, invalidation = narrative.build_covered_call_narrative(
        request=body, regime=technical.regime, balanced_candidate=balanced,
        has_earnings_before_expiration=has_earnings,
    )
    return StrategyAnalysisResponse(
        ticker=ticker,
        strategy=StrategyType.COVERED_CALL,
        regime=technical.regime,
        status=status,
        conviction_score=best_score or 0,
        summary=narrative.build_summary(ticker, technical.regime, status, best_score),
        reasons_for=reasons_for,
        reasons_against=reasons_against,
        risks=risks,
        invalidation_conditions=invalidation,
        next_important_event=narrative.next_event_text(events),
        volatility=volatility,
        conservative=_to_evaluation(ContractRole.CONSERVADOR, roles["conservative"]),
        balanced=_to_evaluation(ContractRole.EQUILIBRADO, roles["balanced"]),
        aggressive=_to_evaluation(ContractRole.AGRESIVO, roles["aggressive"]),
        best_balance=_best_balance_role(roles["conservative"], roles["balanced"], roles["aggressive"]),
        alternative_action=(
            "Esperar un contrato con mejor puntuación o revisar en unos días."
            if status in (StrategyStatus.ESPERAR, StrategyStatus.NO_APLICA)
            else "Considerar esperar si el mercado se vuelve menos favorable antes de ejecutar."
        ),
        data_source_status=technical.data_source_status,
        is_demo=snapshot.chain.is_demo,
        updated_at_ny=technical.updated_at_ny,
    )


def build_csp(snapshot: MarketSnapshot, body: CashSecuredPutRequest) -> StrategyAnalysisResponse:
    ticker = snapshot.quote.ticker
    quote = snapshot.quote
    events = snapshot.events
    technical, _score = build_technical_analysis(quote, snapshot.bars, events)
    has_earnings = _has_earnings_within_window(events, _EARNINGS_RELEVANT_WINDOW_DAYS)
    candidates = csp_engine.select_csp_candidates(
        snapshot.chain.contracts,
        request=body,
        price=quote.price,
        supports=technical.supports,
        has_earnings_before_expiration=has_earnings,
    )
    roles = csp_engine.pick_three_roles(candidates)
    balanced = roles["balanced"]
    best_score = balanced.breakdown.total_score if balanced else None
    status = csp_engine.determine_status(best_score)
    volatility = build_volatility_analysis(snapshot.chain, quote.price, technical.historical_volatility_pct, has_earnings)
    reasons_for, reasons_against, risks, invalidation = narrative.build_csp_narrative(
        request=body, regime=technical.regime, balanced_candidate=balanced,
        has_earnings_before_expiration=has_earnings,
    )
    return StrategyAnalysisResponse(
        ticker=ticker,
        strategy=StrategyType.CASH_SECURED_PUT,
        regime=technical.regime,
        status=status,
        conviction_score=best_score or 0,
        summary=narrative.build_summary(ticker, technical.regime, status, best_score),
        reasons_for=reasons_for,
        reasons_against=reasons_against,
        risks=risks,
        invalidation_conditions=invalidation,
        next_important_event=narrative.next_event_text(events),
        volatility=volatility,
        conservative=_to_evaluation(ContractRole.CONSERVADOR, roles["conservative"]),
        balanced=_to_evaluation(ContractRole.EQUILIBRADO, roles["balanced"]),
        aggressive=_to_evaluation(ContractRole.AGRESIVO, roles["aggressive"]),
        best_balance=_best_balance_role(roles["conservative"], roles["balanced"], roles["aggressive"]),
        alternative_action=(
            "Esperar un contrato con mejor puntuación, o reunir más capital si la limitante fue el efectivo disponible."
            if status in (StrategyStatus.ESPERAR, StrategyStatus.NO_APLICA)
            else "Considerar esperar si el mercado se vuelve menos favorable antes de ejecutar."
        ),
        data_source_status=technical.data_source_status,
        is_demo=snapshot.chain.is_demo,
        updated_at_ny=technical.updated_at_ny,
    )


def dashboard_from_snapshot(snapshot):
    underlying = build_underlying(snapshot)
    return {
        "ticker": snapshot.quote.ticker,
        "quote": snapshot.quote.model_dump(mode="json"),
        "underlying": underlying.model_dump(mode="json"),
        "chain": snapshot.chain.model_dump(mode="json"),
        "options_live": snapshot.options_live,
        "options_note": (
            "Cadena de opciones real de Polygon."
            if snapshot.options_live
            else "DEMO DATA — NOT LIVE. Cadena ilustrativa anclada al precio real del subyacente."
        ),
        "disclaimer": (
            "Premium Income es análisis educativo de Covered Calls y Cash-Secured Puts. "
            "No ejecuta órdenes ni es asesoramiento financiero personalizado."
        ),
    }


async def load_dashboard(ticker: str) -> dict:
    snapshot = await load_market(ticker)
    return dashboard_from_snapshot(snapshot)
