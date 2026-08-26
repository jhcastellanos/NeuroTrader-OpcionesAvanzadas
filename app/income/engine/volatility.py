from typing import Optional
"""
Analisis de volatilidad (seccion 6 del brief).

expected_move = spot * IV * sqrt(DTE / 365) — formula explicita del brief.

LIMITACION DOCUMENTADA: IV Rank e IV Percentile requieren una serie
historica de IV (tipicamente 252 sesiones) que esta app todavia no
almacena (no hay tabla de snapshots historicos de IV en Fase 3). Se
devuelven como None con una nota clara, en vez de aproximarlos con un
numero que pareceria real sin serlo. Ver docs/roadmap.md.
"""
import math
from dataclasses import dataclass


IV_RANK_LIMITATION_NOTE = (
    "IV Rank e IV Percentile requieren una serie historica de IV (min. "
    "252 sesiones) que esta app aun no almacena. Se implementaran cuando "
    "exista persistencia de snapshots diarios de IV (ver docs/roadmap.md)."
)


def expected_move(spot: float, iv: float, dte: int) -> Optional[tuple[float, float]]:
    """
    Devuelve (movimiento_esperado_en_dolares, movimiento_esperado_en_%).
    iv debe ser un decimal (0.30 = 30%), no un porcentaje.
    """
    if spot <= 0 or iv <= 0 or dte <= 0:
        return None
    move_dollars = spot * iv * math.sqrt(dte / 365)
    move_pct = (move_dollars / spot) * 100
    return round(move_dollars, 2), round(move_pct, 2)


def interpret_volatility(
    *,
    historical_volatility_pct: Optional[float],
    atm_iv_pct: Optional[float],
    has_earnings_within_dte: bool,
) -> str:
    """
    Clasificacion determinista en lenguaje sencillo, siguiendo la
    distincion explicita del brief entre volatilidad favorable, riesgo de
    earnings, volatilidad baja poco atractiva y volatilidad extrema.
    IV alta NUNCA se interpreta automaticamente como señal de venta.
    """
    if atm_iv_pct is None:
        if historical_volatility_pct is None:
            return "No hay suficientes datos de volatilidad (ni IV ni historica) para interpretar este activo."
        return (
            f"IV no disponible; unicamente se cuenta con volatilidad historica "
            f"({historical_volatility_pct}%) como referencia."
        )

    if has_earnings_within_dte and historical_volatility_pct and atm_iv_pct > historical_volatility_pct * 1.3:
        return (
            f"La IV ({atm_iv_pct}%) esta notablemente por encima de la volatilidad "
            f"historica ({historical_volatility_pct}%), probablemente por el riesgo de "
            f"earnings dentro del plazo del contrato, no por una oportunidad estructural "
            f"de vender prima."
        )

    if atm_iv_pct > 70:
        return f"Volatilidad extrema (IV {atm_iv_pct}%): el riesgo puede superar el rendimiento esperado de vender prima."

    if historical_volatility_pct and atm_iv_pct >= historical_volatility_pct * 1.1 and 20 <= atm_iv_pct <= 70:
        return f"Volatilidad favorable para vender prima: IV ({atm_iv_pct}%) por encima de la historica, sin ser extrema."

    if atm_iv_pct < 18:
        return f"Volatilidad baja (IV {atm_iv_pct}%): las primas disponibles pueden ser poco atractivas."

    return f"Volatilidad dentro de un rango normal (IV {atm_iv_pct}%)."


@dataclass
class SkewInput:
    call_iv_pct: Optional[float]
    put_iv_pct: Optional[float]


def put_call_skew_pct(skew_input: SkewInput) -> Optional[float]:
    """Diferencia simple put IV - call IV (en puntos porcentuales) para strikes de delta similar."""
    if skew_input.call_iv_pct is None or skew_input.put_iv_pct is None:
        return None
    return round(skew_input.put_iv_pct - skew_input.call_iv_pct, 2)
