from typing import Optional
"""
Regimen de mercado y Underlying Score (0-100).

El score se compone de 6 categorias con puntaje maximo fijo (seccion 5
del brief): Tendencia 25, Momentum 15, Volatilidad 20, Estructura
tecnica 20, Riesgo de eventos 10, Liquidez 10. Cada subfuncion devuelve
(puntos, razon) para que el desglose completo sea explicable — nunca una
caja negra.
"""
from dataclasses import dataclass

from app.income.schemas import MarketRegime, TrendDirection

# Umbrales documentados (no magicos): volatilidad historica anualizada, en %.
_HV_HIGH_THRESHOLD = 55.0
_HV_FAVORABLE_LOW = 18.0
_HV_FAVORABLE_HIGH = 45.0


def classify_regime(
    daily_trend: TrendDirection, historical_volatility_pct: Optional[float]
) -> MarketRegime:
    if historical_volatility_pct is not None and historical_volatility_pct > _HV_HIGH_THRESHOLD:
        return MarketRegime.ALTA_VOLATILIDAD
    if daily_trend == TrendDirection.ALCISTA:
        return MarketRegime.ALCISTA
    if daily_trend == TrendDirection.BAJISTA:
        return MarketRegime.BAJISTA
    return MarketRegime.LATERAL


@dataclass
class ScoreComponent:
    points: int
    max_points: int
    reasons: list[str]


def _score_trend(daily: TrendDirection, weekly: TrendDirection) -> ScoreComponent:
    max_points = 25
    if daily == TrendDirection.INDETERMINADO or weekly == TrendDirection.INDETERMINADO:
        return ScoreComponent(8, max_points, ["Tendencia: datos insuficientes para confirmar (+8/25)."])
    if daily == weekly and daily != TrendDirection.LATERAL:
        return ScoreComponent(
            max_points, max_points,
            [f"Tendencia diaria y semanal alineadas en '{daily}' (+25/25)."],
        )
    if daily == TrendDirection.LATERAL and weekly == TrendDirection.LATERAL:
        return ScoreComponent(12, max_points, ["Tendencia lateral en ambos plazos: sin sesgo claro (+12/25)."])
    if daily != TrendDirection.LATERAL and weekly != TrendDirection.LATERAL and daily != weekly:
        return ScoreComponent(
            5, max_points,
            [f"Tendencia diaria ('{daily}') y semanal ('{weekly}') en conflicto (+5/25)."],
        )
    return ScoreComponent(16, max_points, ["Tendencia parcialmente alineada entre plazos (+16/25)."])


def _score_momentum(rsi_14: Optional[float], macd_histogram: Optional[float]) -> ScoreComponent:
    max_points = 15
    if rsi_14 is None:
        return ScoreComponent(5, max_points, ["Momentum: RSI no disponible (+5/15)."])

    reasons = []
    points = 0
    if 40 <= rsi_14 <= 60:
        points += 8
        reasons.append(f"RSI en zona neutral-saludable ({rsi_14}) (+8/15).")
    elif 30 <= rsi_14 < 40 or 60 < rsi_14 <= 70:
        points += 5
        reasons.append(f"RSI cerca de zona extrema ({rsi_14}) (+5/15).")
    else:
        points += 2
        reasons.append(f"RSI en zona extrema ({rsi_14}): sobrecompra o sobreventa (+2/15).")

    if macd_histogram is not None:
        if macd_histogram > 0:
            points += 4
            reasons.append("MACD con histograma positivo (momentum a favor) (+4).")
        else:
            points += 1
            reasons.append("MACD con histograma negativo (momentum en contra) (+1).")
    else:
        points += 2
        reasons.append("MACD no disponible (+2).")

    return ScoreComponent(min(points, max_points), max_points, reasons)


def _score_volatility(historical_volatility_pct: Optional[float]) -> ScoreComponent:
    max_points = 20
    if historical_volatility_pct is None:
        return ScoreComponent(8, max_points, ["Volatilidad: dato historico insuficiente (+8/20)."])

    hv = historical_volatility_pct
    if _HV_FAVORABLE_LOW <= hv <= _HV_FAVORABLE_HIGH:
        return ScoreComponent(
            max_points, max_points,
            [f"Volatilidad historica en rango favorable para vender prima ({hv}%) (+20/20)."],
        )
    if hv < _HV_FAVORABLE_LOW:
        return ScoreComponent(8, max_points, [f"Volatilidad historica baja ({hv}%): primas poco atractivas (+8/20)."])
    if hv <= _HV_HIGH_THRESHOLD:
        return ScoreComponent(14, max_points, [f"Volatilidad historica moderadamente alta ({hv}%) (+14/20)."])
    return ScoreComponent(4, max_points, [f"Volatilidad historica extrema ({hv}%): riesgo elevado (+4/20)."])


def _score_structure(supports: list[float], resistances: list[float]) -> ScoreComponent:
    max_points = 20
    levels_found = len(supports) + len(resistances)
    if levels_found == 0:
        return ScoreComponent(6, max_points, ["Estructura: no se detectaron soportes/resistencias claros (+6/20)."])
    if levels_found == 1:
        return ScoreComponent(12, max_points, ["Estructura: se detecto un nivel tecnico relevante (+12/20)."])
    return ScoreComponent(
        max_points, max_points,
        [f"Estructura: se detectaron {len(supports)} soporte(s) y {len(resistances)} resistencia(s) (+20/20)."],
    )


def _score_event_risk(has_earnings_within_30d: bool) -> ScoreComponent:
    max_points = 10
    if has_earnings_within_30d:
        return ScoreComponent(3, max_points, ["Riesgo de eventos: earnings dentro de los proximos 30 dias (+3/10)."])
    return ScoreComponent(max_points, max_points, ["Riesgo de eventos: sin earnings inminentes conocidos (+10/10)."])


def _score_liquidity(volume: int, relative_volume: Optional[float]) -> ScoreComponent:
    max_points = 10
    if volume <= 0:
        return ScoreComponent(0, max_points, ["Liquidez: sin volumen registrado (+0/10)."])

    points = 0
    reasons = []
    if volume >= 5_000_000:
        points += 6
        reasons.append(f"Volumen alto ({volume:,}) (+6).")
    elif volume >= 1_000_000:
        points += 4
        reasons.append(f"Volumen moderado ({volume:,}) (+4).")
    else:
        points += 1
        reasons.append(f"Volumen bajo ({volume:,}) (+1).")

    if relative_volume is not None:
        if 0.7 <= relative_volume <= 1.8:
            points += 4
            reasons.append(f"Volumen relativo normal ({relative_volume}x) (+4).")
        else:
            points += 1
            reasons.append(f"Volumen relativo atipico ({relative_volume}x) (+1).")
    else:
        points += 2
        reasons.append("Volumen relativo no disponible (+2).")

    return ScoreComponent(min(points, max_points), max_points, reasons)


@dataclass
class UnderlyingScoreResult:
    trend: ScoreComponent
    momentum: ScoreComponent
    volatility: ScoreComponent
    structure: ScoreComponent
    event_risk: ScoreComponent
    liquidity: ScoreComponent

    @property
    def total(self) -> int:
        return (
            self.trend.points
            + self.momentum.points
            + self.volatility.points
            + self.structure.points
            + self.event_risk.points
            + self.liquidity.points
        )

    @property
    def all_reasons(self) -> list[str]:
        reasons: list[str] = []
        for component in (
            self.trend, self.momentum, self.volatility,
            self.structure, self.event_risk, self.liquidity,
        ):
            reasons.extend(component.reasons)
        return reasons


def compute_underlying_score(
    *,
    daily_trend: TrendDirection,
    weekly_trend: TrendDirection,
    rsi_14: Optional[float],
    macd_histogram: Optional[float],
    historical_volatility_pct: Optional[float],
    supports: list[float],
    resistances: list[float],
    has_earnings_within_30d: bool,
    volume: int,
    relative_volume: Optional[float],
) -> UnderlyingScoreResult:
    return UnderlyingScoreResult(
        trend=_score_trend(daily_trend, weekly_trend),
        momentum=_score_momentum(rsi_14, macd_histogram),
        volatility=_score_volatility(historical_volatility_pct),
        structure=_score_structure(supports, resistances),
        event_risk=_score_event_risk(has_earnings_within_30d),
        liquidity=_score_liquidity(volume, relative_volume),
    )
