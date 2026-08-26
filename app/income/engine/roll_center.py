from typing import Optional
"""
Roll Center (seccion 12 del brief): "No hagas un roll automatico.
Presenta alternativas con debito/credito neto y solicita decision del
usuario." Este modulo solo genera candidatos y calcula el numero; la
decision de ejecutar (fuera del alcance del MVP, seccion 19) siempre
queda del lado del usuario.
"""
from dataclasses import dataclass
from datetime import date

from app.income.schemas import OptionContract


@dataclass
class RollCandidate:
    contract: OptionContract
    reason: str


def build_roll_candidates(
    chain_contracts: list[OptionContract],
    *,
    contract_type: str,
    current_strike: float,
    current_expiration: date,
    max_future_expirations: int = 2,
) -> list[RollCandidate]:
    """
    Selecciona hasta `max_future_expirations` vencimientos posteriores al
    actual, y para cada uno propone: (1) el strike mas parecido al
    actual, y (2) un strike "mejorado" (mas alejado del precio, menor
    delta) si existe. Nunca inventa un contrato: si no hay candidatos
    validos en la cadena, la lista sale vacia.
    """
    future = [c for c in chain_contracts if c.contract_type == contract_type and c.expiration_date > current_expiration]
    if not future:
        return []

    expirations = sorted({c.expiration_date for c in future})[:max_future_expirations]
    picks: list[RollCandidate] = []

    for exp in expirations:
        same_exp = [c for c in future if c.expiration_date == exp]
        if not same_exp:
            continue

        same_strike = min(same_exp, key=lambda c: abs(c.strike - current_strike))
        picks.append(RollCandidate(same_strike, f"Mismo strike aproximado, vencimiento {exp.isoformat()}."))

        if contract_type == "call":
            improved = [c for c in same_exp if c.strike > current_strike]
            improved_pick = min(improved, key=lambda c: c.strike) if improved else None
        else:
            improved = [c for c in same_exp if c.strike < current_strike]
            improved_pick = max(improved, key=lambda c: c.strike) if improved else None

        if improved_pick and improved_pick.occ_symbol != same_strike.occ_symbol:
            picks.append(
                RollCandidate(improved_pick, f"Strike mas alejado (menor riesgo de asignacion), vencimiento {exp.isoformat()}.")
            )

    return picks


def compute_net_credit_debit(closing_cost: Optional[float], opening_credit: Optional[float]) -> Optional[float]:
    """
    closing_cost = costo de recomprar el contrato actual (debito).
    opening_credit = prima recibida al abrir el nuevo contrato (credito).
    Positivo = roll por credito neto; negativo = roll por debito neto.
    """
    if closing_cost is None or opening_credit is None:
        return None
    return round(opening_credit - closing_cost, 4)
