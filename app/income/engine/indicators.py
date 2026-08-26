from typing import Optional
"""
Indicadores tecnicos — funciones puras y deterministicas.

Regla de oro (seccion 5 del brief): "No permitas que la IA invente
indicadores. Todos los numeros deben proceder de calculos deterministicos
o de datos de mercado validados." Nada en este archivo llama a la IA ni
a ningun proveedor de datos; solo recibe listas de precios/velas y
devuelve numeros, con pruebas unitarias en tests/engine/test_indicators.py.

LIMITACION DOCUMENTADA: VWAP normalmente se calcula sobre datos
intradia (por operacion). Aqui se aproxima con precio tipico
((H+L+C)/3) ponderado por volumen sobre velas DIARIAS, lo cual es una
aproximacion de mas largo plazo, no el VWAP intradia clasico usado por
traders de corto plazo. Se documenta como `vwap_approx`.
"""
import math
from dataclasses import dataclass

from app.income.schemas import Bar, TrendDirection


def sma(values: list[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    return round(sum(values[-period:]) / period, 4)


def ema_series(values: list[float], period: int) -> list[float]:
    """
    Serie completa de EMA. Semilla = primer valor de la serie (en vez de
    una SMA inicial) para poder operar con series cortas; es una
    aproximacion estandar aceptable para este proposito y se documenta.
    """
    if not values or period <= 0:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for price in values[1:]:
        out.append(price * k + out[-1] * (1 - k))
    return out


def ema(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return round(ema_series(values, period)[-1], 4)


def rsi(values: list[float], period: int = 14) -> Optional[float]:
    """RSI de Wilder (suavizado exponencial de ganancias/perdidas)."""
    if len(values) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


@dataclass
class MacdResult:
    macd: float
    signal: float
    histogram: float


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Optional[MacdResult]:
    if len(values) < slow + signal:
        return None
    fast_ema = ema_series(values, fast)
    slow_ema = ema_series(values, slow)
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = ema_series(macd_line, signal)
    macd_val = macd_line[-1]
    signal_val = signal_line[-1]
    return MacdResult(
        macd=round(macd_val, 4),
        signal=round(signal_val, 4),
        histogram=round(macd_val - signal_val, 4),
    )


def atr(bars: list[Bar], period: int = 14) -> Optional[float]:
    """Average True Range con suavizado de Wilder."""
    if len(bars) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    atr_val = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return round(atr_val, 4)


def vwap_approx(bars: list[Bar], period: int = 20) -> Optional[float]:
    recent = bars[-period:] if len(bars) >= period else bars
    if not recent:
        return None
    total_volume = sum(b.volume for b in recent)
    if total_volume == 0:
        return None
    typical_sum = sum(((b.high + b.low + b.close) / 3) * b.volume for b in recent)
    return round(typical_sum / total_volume, 2)


def historical_volatility_pct(
    values: list[float], period: int = 20, trading_days: int = 252
) -> Optional[float]:
    """Volatilidad historica anualizada (%) a partir de retornos logaritmicos."""
    if len(values) < period + 1:
        return None
    recent = values[-(period + 1):]
    log_returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent))]
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_std = math.sqrt(variance)
    return round(daily_std * math.sqrt(trading_days) * 100, 2)


def find_pivots(bars: list[Bar], window: int = 3) -> tuple[list[int], list[int]]:
    """Indices de swing highs/lows: maximo o minimo local dentro de `window` barras."""
    high_idx: list[int] = []
    low_idx: list[int] = []
    n = len(bars)
    for i in range(window, n - window):
        segment = bars[i - window : i + window + 1]
        if bars[i].high == max(b.high for b in segment):
            high_idx.append(i)
        if bars[i].low == min(b.low for b in segment):
            low_idx.append(i)
    return high_idx, low_idx


def find_supports_resistances(
    bars: list[Bar],
    current_price: float,
    lookback: int = 90,
    window: int = 3,
    max_levels: int = 3,
) -> tuple[list[float], list[float]]:
    """
    Soportes/resistencias por deteccion simple de swing highs/lows.
    LIMITACION: no agrupa niveles cercanos (clustering) ni pondera por
    numero de toques; es una primera aproximacion util para la UI. Se
    puede refinar en una fase de calidad posterior.
    """
    recent = bars[-lookback:] if len(bars) > lookback else bars
    if len(recent) < window * 2 + 1:
        return [], []

    high_idx, low_idx = find_pivots(recent, window)
    resistances = sorted({round(recent[i].high, 2) for i in high_idx if recent[i].high > current_price})
    supports = sorted(
        {round(recent[i].low, 2) for i in low_idx if recent[i].low < current_price}, reverse=True
    )
    return supports[:max_levels], resistances[:max_levels]


def classify_daily_trend(
    price: float, ema20: Optional[float], ema50: Optional[float], sma200: Optional[float]
) -> TrendDirection:
    if ema20 is None or ema50 is None or sma200 is None:
        return TrendDirection.INDETERMINADO
    if price > ema20 > ema50 > sma200:
        return TrendDirection.ALCISTA
    if price < ema20 < ema50 < sma200:
        return TrendDirection.BAJISTA
    return TrendDirection.LATERAL


def classify_weekly_trend(closes: list[float]) -> TrendDirection:
    """
    Aproximacion: remuestrea cierres diarios cada 5 barras (~1 semana
    bursatil) y compara el EMA reciente contra el EMA previo de esa
    serie semanal. No es un remuestreo OHLC real por semana calendario;
    se documenta como aproximacion.
    """
    weekly_closes = closes[::5]
    if len(weekly_closes) < 5:
        return TrendDirection.INDETERMINADO

    period = min(4, len(weekly_closes) - 1)
    recent_ema = ema(weekly_closes, period)
    prior_ema = ema(weekly_closes[:-1], period)
    if recent_ema is None or prior_ema is None or prior_ema == 0:
        return TrendDirection.INDETERMINADO
    change = (recent_ema - prior_ema) / abs(prior_ema)
    if change > 0.01:
        return TrendDirection.ALCISTA
    if change < -0.01:
        return TrendDirection.BAJISTA
    return TrendDirection.LATERAL
