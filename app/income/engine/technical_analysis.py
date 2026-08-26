from typing import Optional
"""
Compone TechnicalAnalysis + UnderlyingScoreBreakdown a partir de una
Quote, una BarsResponse y (opcionalmente) CorporateEventsResponse. Es la
capa que conecta los indicadores puros (indicators.py) y el score
(underlying_score.py) con los modelos de la API.
"""
from datetime import date

from app.income.engine import indicators as ind
from app.income.engine.underlying_score import classify_regime, compute_underlying_score
from app.income.schemas import (
    BarsResponse,
    CorporateEventsResponse,
    Quote,
    TechnicalAnalysis,
    UnderlyingScoreBreakdown,
)


def _has_earnings_within(events: Optional[CorporateEventsResponse], days: int) -> bool:
    if not events or not events.next_earnings_date:
        return False
    return (events.next_earnings_date - date.today()).days <= days


def build_technical_analysis(
    quote: Quote, bars: BarsResponse, events: Optional[CorporateEventsResponse]
) -> tuple[TechnicalAnalysis, UnderlyingScoreBreakdown]:
    closes = [b.close for b in bars.bars]
    price = quote.price

    sma_200 = ind.sma(closes, 200)
    ema_9 = ind.ema(closes, 9)
    ema_20 = ind.ema(closes, 20)
    ema_50 = ind.ema(closes, 50)
    rsi_14 = ind.rsi(closes, 14)
    macd_result = ind.macd(closes)
    atr_14 = ind.atr(bars.bars, 14)
    vwap = ind.vwap_approx(bars.bars, 20)
    hv = ind.historical_volatility_pct(closes, 20)
    supports, resistances = ind.find_supports_resistances(bars.bars, price)
    daily_trend = ind.classify_daily_trend(price, ema_20, ema_50, sma_200)
    weekly_trend = ind.classify_weekly_trend(closes)
    regime = classify_regime(daily_trend, hv)

    distance_to_key_levels: dict[str, float] = {}
    for label, value in (("ema_20", ema_20), ("ema_50", ema_50), ("sma_200", sma_200)):
        if value:
            distance_to_key_levels[label] = round((price - value) / value * 100, 2)

    has_earnings_30d = _has_earnings_within(events, 30)

    technical = TechnicalAnalysis(
        ticker=quote.ticker,
        price=price,
        sma_200=sma_200,
        ema_9=ema_9,
        ema_20=ema_20,
        ema_50=ema_50,
        rsi_14=rsi_14,
        macd=macd_result.macd if macd_result else None,
        macd_signal=macd_result.signal if macd_result else None,
        macd_histogram=macd_result.histogram if macd_result else None,
        atr_14=atr_14,
        vwap_approx=vwap,
        week52_high=quote.week52_high,
        week52_low=quote.week52_low,
        historical_volatility_pct=hv,
        daily_trend=daily_trend,
        weekly_trend=weekly_trend,
        distance_to_key_levels_pct=distance_to_key_levels,
        supports=supports,
        resistances=resistances,
        regime=regime,
        bars_used=len(bars.bars),
        data_source_status=quote.data_source_status,
        is_demo=quote.is_demo,
        updated_at_ny=quote.updated_at_ny,
    )

    score_result = compute_underlying_score(
        daily_trend=daily_trend,
        weekly_trend=weekly_trend,
        rsi_14=rsi_14,
        macd_histogram=macd_result.histogram if macd_result else None,
        historical_volatility_pct=hv,
        supports=supports,
        resistances=resistances,
        has_earnings_within_30d=has_earnings_30d,
        volume=quote.volume,
        relative_volume=quote.relative_volume,
    )

    score = UnderlyingScoreBreakdown(
        trend_score=score_result.trend.points,
        momentum_score=score_result.momentum.points,
        volatility_score=score_result.volatility.points,
        structure_score=score_result.structure.points,
        event_risk_score=score_result.event_risk.points,
        liquidity_score=score_result.liquidity.points,
        total_score=score_result.total,
        reasons=score_result.all_reasons,
    )

    return technical, score
