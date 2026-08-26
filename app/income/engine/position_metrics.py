from typing import Optional
"""
Metricas de posiciones abiertas (seccion 13 del brief) — funciones puras.

ALCANCE DOCUMENTADO: `pnl_current` y `cumulative_return_pct` cubren
UNICAMENTE la pata de opcion (prima cobrada vs. costo de recompra
actual), no el P&L combinado de las acciones subyacentes en Covered
Call. Rastrear el P&L de las acciones requeriria el precio de mercado
en tiempo real de cada lote, lo cual se puede añadir en una fase
posterior; por ahora se prioriza la metrica mas directamente accionable
para quien vende prima: cuanto de la prima ya se capturo.
"""
from dataclasses import dataclass
from datetime import date

from app.income.schemas import PositionStatus

# Umbral de "beneficio objetivo alcanzado", configurable, documentado en
# la seccion 12 del brief ("considerar cierre al capturar 50% de la prima").
_PROFIT_TARGET_PCT = 50.0
_NEAR_EXPIRATION_DAYS = 7
_TERMINAL_STATUSES = {PositionStatus.CERRADA, PositionStatus.ASIGNADA, PositionStatus.ROLLED}


@dataclass
class LiveMetrics:
    days_remaining: int
    pct_premium_captured: Optional[float]
    pnl_current: Optional[float]
    distance_to_strike_pct: Optional[float]
    extrinsic_value_remaining: Optional[float]
    cumulative_return_pct: Optional[float]
    assignment_risk_note: str


def compute_days_remaining(expiration_date: date, as_of: Optional[date] = None) -> int:
    today = as_of or date.today()
    return (expiration_date - today).days


def compute_pct_premium_captured(premium_received: float, current_premium: Optional[float]) -> Optional[float]:
    if current_premium is None or premium_received <= 0:
        return None
    return round(((premium_received - current_premium) / premium_received) * 100, 2)


def compute_pnl_current(
    premium_received: float, current_premium: Optional[float], contracts: int, commissions: float
) -> Optional[float]:
    if current_premium is None:
        return None
    return round((premium_received - current_premium) * 100 * contracts - commissions, 2)


def compute_distance_to_strike_pct(current_price: Optional[float], strike: float) -> Optional[float]:
    if current_price is None or strike <= 0:
        return None
    return round(((current_price - strike) / strike) * 100, 2)


def compute_extrinsic_value_remaining(
    current_premium: Optional[float], current_price: Optional[float], strike: float, contract_type: str
) -> Optional[float]:
    if current_premium is None or current_price is None:
        return None
    if contract_type == "call":
        intrinsic = max(current_price - strike, 0.0)
    else:
        intrinsic = max(strike - current_price, 0.0)
    return round(max(current_premium - intrinsic, 0.0), 4)


def compute_cumulative_return_pct(pnl_current: Optional[float], capital_basis: Optional[float]) -> Optional[float]:
    if pnl_current is None or not capital_basis:
        return None
    return round((pnl_current / capital_basis) * 100, 2)


def is_itm(current_price: Optional[float], strike: float, contract_type: str) -> bool:
    if current_price is None:
        return False
    if contract_type == "call":
        return current_price > strike
    return current_price < strike


def build_assignment_risk_note(
    current_price: Optional[float], strike: float, contract_type: str, days_remaining: int
) -> str:
    if current_price is None:
        return "No hay precio actual disponible para estimar el riesgo de asignacion."
    itm = is_itm(current_price, strike, contract_type)
    if itm and days_remaining <= 7:
        return "Riesgo de asignacion alto: el contrato esta ITM y vence pronto."
    if itm:
        return "El contrato esta ITM; hay riesgo de asignacion si se mantiene hasta el vencimiento."
    distance = compute_distance_to_strike_pct(current_price, strike) or 0
    if abs(distance) <= 2:
        return "El precio esta cerca del strike: riesgo de asignacion moderado, vigilar de cerca."
    return "El contrato esta OTM con margen razonable: riesgo de asignacion bajo por ahora."


def compute_live_metrics(
    *,
    premium_received: float,
    current_premium: Optional[float],
    contracts: int,
    commissions: float,
    current_price: Optional[float],
    strike: float,
    contract_type: str,
    capital_basis: Optional[float],
    expiration_date: date,
    as_of: Optional[date] = None,
) -> LiveMetrics:
    days_remaining = compute_days_remaining(expiration_date, as_of)
    pct_captured = compute_pct_premium_captured(premium_received, current_premium)
    pnl = compute_pnl_current(premium_received, current_premium, contracts, commissions)
    distance = compute_distance_to_strike_pct(current_price, strike)
    extrinsic = compute_extrinsic_value_remaining(current_premium, current_price, strike, contract_type)
    cumulative_return = compute_cumulative_return_pct(pnl, capital_basis)
    risk_note = build_assignment_risk_note(current_price, strike, contract_type, days_remaining)

    return LiveMetrics(
        days_remaining=days_remaining,
        pct_premium_captured=pct_captured,
        pnl_current=pnl,
        distance_to_strike_pct=distance,
        extrinsic_value_remaining=extrinsic,
        cumulative_return_pct=cumulative_return,
        assignment_risk_note=risk_note,
    )


def compute_suggested_status(
    *,
    stored_status: PositionStatus,
    current_price: Optional[float],
    strike: float,
    contract_type: str,
    days_remaining: int,
    pct_premium_captured: Optional[float],
    has_earnings_before_expiration: bool,
) -> PositionStatus:
    """
    Si el estado guardado ya es terminal (cerrada/asignada/rolled), se
    respeta tal cual: esos estados solo los define el usuario
    explicitamente. Para el resto, se sugiere el estado mas urgente
    segun esta prioridad: ITM > proxima a vencimiento > proxima a
    earnings > beneficio objetivo alcanzado > abierta.
    """
    if stored_status in _TERMINAL_STATUSES:
        return stored_status

    if is_itm(current_price, strike, contract_type):
        return PositionStatus.ITM
    if 0 <= days_remaining <= _NEAR_EXPIRATION_DAYS:
        return PositionStatus.PROXIMA_VENCIMIENTO
    if has_earnings_before_expiration:
        return PositionStatus.PROXIMA_EARNINGS
    if pct_premium_captured is not None and pct_premium_captured >= _PROFIT_TARGET_PCT:
        return PositionStatus.BENEFICIO_OBJETIVO
    return PositionStatus.ABIERTA
