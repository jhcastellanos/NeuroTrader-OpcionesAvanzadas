from typing import Optional
"""
Modelo Black-Scholes — funciones puras.

Se usa UNICAMENTE para el motor de backtesting (seccion 14 del brief),
porque no existe una fuente de datos historicos de opciones reales
integrada (Polygon y el modo demo solo dan snapshots del momento
actual). Black-Scholes con volatilidad historica causal (sin mirar el
futuro) es una aproximacion estandar para simular primas pasadas de
forma razonable — NUNCA se presenta como precio real de mercado.

No se usa en ningun otro lugar de la app: el analisis en vivo (Fases
2-4) siempre usa datos crudos del proveedor (bid/ask/mid reales o demo),
nunca un modelo teorico.
"""
import math
from dataclasses import dataclass


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


@dataclass
class BSInputs:
    spot: float
    strike: float
    dte_days: int
    iv: float  # decimal, ej. 0.30
    risk_free_rate: float = 0.04
    contract_type: str = "call"


def _d1_d2(inputs: BSInputs) -> Optional[tuple[float, float]]:
    if inputs.spot <= 0 or inputs.strike <= 0 or inputs.iv <= 0 or inputs.dte_days <= 0:
        return None
    t = inputs.dte_days / 365
    sigma_sqrt_t = inputs.iv * math.sqrt(t)
    if sigma_sqrt_t == 0:
        return None
    d1 = (
        math.log(inputs.spot / inputs.strike)
        + (inputs.risk_free_rate + 0.5 * inputs.iv**2) * t
    ) / sigma_sqrt_t
    d2 = d1 - sigma_sqrt_t
    return d1, d2


def bs_price(inputs: BSInputs) -> float:
    """Precio teorico de la opcion. Si dte_days <= 0, devuelve el valor intrinseco."""
    if inputs.dte_days <= 0:
        if inputs.contract_type == "call":
            return max(inputs.spot - inputs.strike, 0.0)
        return max(inputs.strike - inputs.spot, 0.0)

    d = _d1_d2(inputs)
    if d is None:
        if inputs.contract_type == "call":
            return max(inputs.spot - inputs.strike, 0.0)
        return max(inputs.strike - inputs.spot, 0.0)

    d1, d2 = d
    t = inputs.dte_days / 365
    discount = math.exp(-inputs.risk_free_rate * t)

    if inputs.contract_type == "call":
        price = inputs.spot * _norm_cdf(d1) - inputs.strike * discount * _norm_cdf(d2)
    else:
        price = inputs.strike * discount * _norm_cdf(-d2) - inputs.spot * _norm_cdf(-d1)
    return round(max(price, 0.0), 4)


def bs_delta(inputs: BSInputs) -> float:
    if inputs.dte_days <= 0:
        if inputs.contract_type == "call":
            return 1.0 if inputs.spot > inputs.strike else 0.0
        return -1.0 if inputs.spot < inputs.strike else 0.0

    d = _d1_d2(inputs)
    if d is None:
        return 0.0
    d1, _ = d
    if inputs.contract_type == "call":
        return round(_norm_cdf(d1), 4)
    return round(_norm_cdf(d1) - 1, 4)


def strike_for_target_delta(
    spot: float, target_abs_delta: float, dte_days: int, iv: float, contract_type: str,
    risk_free_rate: float = 0.04,
) -> float:
    """
    Busca (por bisección) el strike que produce el delta objetivo, dado
    IV y DTE. Metodo numerico simple porque no existe forma cerrada
    directa para strike(delta) sin invertir la CDF normal en terminos de
    strike.
    """
    target_abs_delta = max(0.01, min(0.99, target_abs_delta))
    low, high = spot * 0.5, spot * 1.8

    for _ in range(60):
        mid = (low + high) / 2
        inputs = BSInputs(spot=spot, strike=mid, dte_days=dte_days, iv=iv, contract_type=contract_type)
        delta = abs(bs_delta(inputs))
        if abs(delta - target_abs_delta) < 1e-4:
            break
        if contract_type == "call":
            # Delta de call baja al subir el strike.
            if delta > target_abs_delta:
                low = mid
            else:
                high = mid
        else:
            # |Delta| de put baja al subir el strike (put mas OTM cuando strike es menor).
            if delta > target_abs_delta:
                high = mid
            else:
                low = mid

    return round((low + high) / 2, 2)
