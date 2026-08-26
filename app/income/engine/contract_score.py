from typing import Optional
"""
Componentes genericos del Option Opportunity Score (seccion 10 del
brief). La ubicacion tecnica del strike (respecto a resistencia para
Covered Call, respecto a soporte para CSP) y el rendimiento de prima
viven en covered_call.py / csp.py porque su logica difiere por
direccion de la estrategia; el resto de componentes es identico para
ambas y se reutiliza desde aqui.
"""
from dataclasses import dataclass


@dataclass
class ScorePart:
    points: int
    max_points: int
    reasons: list[str]


def score_liquidity(
    open_interest: Optional[int], volume: Optional[int], spread_percent: Optional[float]
) -> ScorePart:
    max_points = 20
    reasons: list[str] = []
    points = 0

    if not open_interest:
        reasons.append("Open interest en cero o desconocido: liquidez muy pobre (+0/8).")
    elif open_interest < 50:
        points += 3
        reasons.append(f"Open interest bajo ({open_interest}) (+3/8).")
    elif open_interest < 500:
        points += 6
        reasons.append(f"Open interest moderado ({open_interest}) (+6/8).")
    else:
        points += 8
        reasons.append(f"Open interest solido ({open_interest}) (+8/8).")

    if not volume:
        reasons.append("Sin volumen negociado en la sesion (+0/6).")
    elif volume < 10:
        points += 2
        reasons.append(f"Volumen bajo ({volume}) (+2/6).")
    else:
        points += 6
        reasons.append(f"Volumen saludable ({volume}) (+6/6).")

    if spread_percent is None:
        points += 2
        reasons.append("Spread no disponible (+2/6).")
    elif spread_percent <= 5:
        points += 6
        reasons.append(f"Spread ajustado ({spread_percent}%) (+6/6).")
    elif spread_percent <= 12:
        points += 3
        reasons.append(f"Spread moderado ({spread_percent}%) (+3/6).")
    else:
        reasons.append(f"Spread amplio ({spread_percent}%): penalizado (+0/6).")

    return ScorePart(min(points, max_points), max_points, reasons)


def score_dte(dte: int, preferred_low: int = 21, preferred_high: int = 45) -> ScorePart:
    max_points = 10
    if preferred_low <= dte <= preferred_high:
        return ScorePart(
            max_points, max_points,
            [f"DTE ({dte}) dentro del rango preferido {preferred_low}-{preferred_high} (+10/10)."],
        )
    distance = min(abs(dte - preferred_low), abs(dte - preferred_high))
    if distance <= 7:
        return ScorePart(6, max_points, [f"DTE ({dte}) cerca del rango preferido (+6/10)."])
    if dte < preferred_low:
        return ScorePart(3, max_points, [f"DTE ({dte}) muy corto: mayor riesgo de gamma cerca del vencimiento (+3/10)."])
    return ScorePart(4, max_points, [f"DTE ({dte}) mas largo de lo preferido: capital comprometido mas tiempo (+4/10)."])


def score_volatility_component(implied_volatility_pct: Optional[float]) -> ScorePart:
    max_points = 10
    if implied_volatility_pct is None:
        return ScorePart(4, max_points, ["IV del contrato no disponible (+4/10)."])
    iv = implied_volatility_pct
    if 20 <= iv <= 60:
        return ScorePart(max_points, max_points, [f"IV del contrato en rango favorable ({iv}%) (+10/10)."])
    if iv < 20:
        return ScorePart(5, max_points, [f"IV baja ({iv}%): prima poco atractiva (+5/10)."])
    return ScorePart(3, max_points, [f"IV extrema ({iv}%): riesgo elevado (+3/10)."])


def score_delta_band(delta: Optional[float], target_low: float, target_high: float) -> ScorePart:
    max_points = 15
    if delta is None:
        return ScorePart(5, max_points, ["Delta no disponible para este contrato (+5/15)."])
    abs_delta = round(abs(delta), 4)
    if target_low <= abs_delta <= target_high:
        return ScorePart(
            max_points, max_points,
            [f"Delta ({abs_delta}) dentro del objetivo {target_low}-{target_high} del perfil de riesgo (+15/15)."],
        )
    distance = min(abs(abs_delta - target_low), abs(abs_delta - target_high))
    if distance <= 0.05:
        return ScorePart(9, max_points, [f"Delta ({abs_delta}) cerca del objetivo del perfil (+9/15)."])
    return ScorePart(3, max_points, [f"Delta ({abs_delta}) fuera del objetivo del perfil de riesgo elegido (+3/15)."])


def score_event_risk(has_earnings_before_expiration: bool, accepted_by_user: bool) -> ScorePart:
    max_points = 10
    if has_earnings_before_expiration and not accepted_by_user:
        return ScorePart(
            2, max_points,
            ["Earnings antes del vencimiento y el usuario NO lo acepto explicitamente (+2/10)."],
        )
    if has_earnings_before_expiration and accepted_by_user:
        return ScorePart(
            6, max_points,
            ["Earnings antes del vencimiento, aceptado explicitamente por el usuario (+6/10)."],
        )
    return ScorePart(max_points, max_points, ["Sin earnings conocidos antes del vencimiento (+10/10)."])


def classify_total_score(total: int) -> str:
    if total >= 85:
        return "Excelente configuracion"
    if total >= 75:
        return "Buena configuracion"
    if total >= 65:
        return "Aceptable con precaucion"
    if total >= 50:
        return "Debil"
    return "No recomendada"
