from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Dict, Any
import numpy as np
import pandas as pd

@dataclass
class Level:
    side: str
    low: float
    high: float
    score: int
    center: float
    reasons: List[str]
    touches: int

def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def _rolling_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    return pv.cumsum() / df["volume"].replace(0, np.nan).cumsum()

def _swing_points(df: pd.DataFrame, left: int = 3, right: int = 3):
    highs, lows = [], []
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    for i in range(left, len(df)-right):
        if h[i] >= h[i-left:i+right+1].max():
            highs.append((i, float(h[i])))
        if l[i] <= l[i-left:i+right+1].min():
            lows.append((i, float(l[i])))
    return highs, lows

def _volume_poc(df: pd.DataFrame, bins: int = 40) -> float:
    lo = float(df["low"].min())
    hi = float(df["high"].max())
    if hi <= lo:
        return float(df["close"].iloc[-1])
    edges = np.linspace(lo, hi, bins + 1)
    typical = ((df["high"] + df["low"] + df["close"]) / 3).to_numpy()
    vol = df["volume"].to_numpy()
    idx = np.clip(np.digitize(typical, edges) - 1, 0, bins - 1)
    bucket = np.zeros(bins)
    for i, v in zip(idx, vol):
        bucket[i] += v
    j = int(bucket.argmax())
    return float((edges[j] + edges[j+1]) / 2)

def _anchored_vwap(df: pd.DataFrame, anchor_idx: int) -> float:
    sub = df.iloc[anchor_idx:].copy()
    typical = (sub["high"] + sub["low"] + sub["close"]) / 3
    denom = sub["volume"].sum()
    if denom <= 0:
        return float(sub["close"].iloc[-1])
    return float((typical * sub["volume"]).sum() / denom)

def _cluster(points, tolerance):
    if not points:
        return []
    pts = sorted(points)
    clusters = [[pts[0]]]
    for p in pts[1:]:
        if abs(p - np.mean(clusters[-1])) <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return clusters

def _touch_count(df, center, tol):
    c = df["close"]
    return int(((c - center).abs() <= tol).sum())

def _make_candidates(df: pd.DataFrame) -> Dict[str, Any]:
    price = float(df["close"].iloc[-1])
    atr_s = _atr(df)
    atr = float(atr_s.iloc[-1]) if pd.notna(atr_s.iloc[-1]) else price * 0.03
    atr = max(atr, price * 0.003)
    vwap = float(_rolling_vwap(df).iloc[-1])
    sma = {}
    for n in [20,50,100,200]:
        sma[n] = float(df["close"].rolling(n).mean().iloc[-1]) if len(df) >= n else float("nan")

    highs, lows = _swing_points(df)
    poc = _volume_poc(df.tail(min(len(df), 180)))
    # Anchor to the most recent significant swing low, else 20 bars back.
    anchor_idx = lows[-1][0] if lows else max(0, len(df)-20)
    avwap = _anchored_vwap(df, anchor_idx)

    recent = df.tail(min(180, len(df)))
    swing_hi = float(recent["high"].max())
    swing_lo = float(recent["low"].min())
    fib = {
        "38.2": swing_hi - (swing_hi - swing_lo) * 0.382,
        "50.0": swing_hi - (swing_hi - swing_lo) * 0.5,
        "61.8": swing_hi - (swing_hi - swing_lo) * 0.618,
    }
    return {
        "price": price, "atr": atr, "vwap": vwap, "avwap": avwap, "sma": sma,
        "poc": poc, "highs": highs, "lows": lows, "fib": fib,
        "swing_hi": swing_hi, "swing_lo": swing_lo
    }

def _score_level(df, center, side, c, tolerance):
    score = 0
    reasons = []
    touches = _touch_count(df.tail(min(len(df), 260)), center, tolerance)

    # Structure 25
    swings = c["lows"] if side == "support" else c["highs"]
    near_swings = sum(1 for _, p in swings[-30:] if abs(p-center) <= tolerance)
    structure_pts = min(25, 8 + near_swings * 5)
    score += structure_pts
    if near_swings:
        reasons.append(f"{near_swings} structural reactions")

    # POC / volume 20
    if abs(c["poc"] - center) <= tolerance * 1.5:
        score += 20
        reasons.append("Volume POC")

    # SMA/VWAP/AVWAP 15
    ma_hits = []
    for n,val in c["sma"].items():
        if math.isfinite(val) and abs(val-center) <= tolerance*1.5:
            ma_hits.append(f"SMA{n}")
    if abs(c["vwap"]-center) <= tolerance*1.5:
        ma_hits.append("VWAP")
    if abs(c["avwap"]-center) <= tolerance*1.5:
        ma_hits.append("Anchored VWAP")
    if ma_hits:
        score += min(15, 5 + 3*len(ma_hits))
        reasons.extend(ma_hits[:4])

    # Reaction count 15
    reaction_pts = min(15, touches * 3)
    score += reaction_pts
    if touches >= 2:
        reasons.append(f"{touches} closes near zone")

    # Fib 5
    fib_hits = [k for k,v in c["fib"].items() if abs(v-center) <= tolerance*1.5]
    if fib_hits:
        score += 5
        reasons.append(f"Fib {fib_hits[0]}%")

    # Round/psychological 5
    round_step = 10 if c["price"] >= 200 else 5 if c["price"] >= 50 else 1
    if abs(center / round_step - round(center / round_step)) <= 0.12:
        score += 5
        reasons.append("Psychological level")

    # Recent volume 5: high-volume bars near the zone
    recent = df.tail(min(len(df), 120))
    vol_ma = recent["volume"].rolling(20).mean()
    mask = (recent["close"]-center).abs() <= tolerance*1.5
    if mask.any():
        idxs = np.where(mask.to_numpy())[0]
        good = False
        for j in idxs:
            if j < len(vol_ma) and pd.notna(vol_ma.iloc[j]) and recent["volume"].iloc[j] > vol_ma.iloc[j] * 1.15:
                good = True; break
        if good:
            score += 5
            reasons.append("Above-average volume")

    return min(100, max(0, int(round(score)))), touches, reasons

def calculate_levels(df: pd.DataFrame) -> Dict[str, Any]:
    df = df.copy().reset_index(drop=True)
    c = _make_candidates(df)
    price, atr = c["price"], c["atr"]
    tolerance = max(atr * 0.35, price * 0.004)

    support_points = [p for _,p in c["lows"][-35:]] + [c["poc"], c["avwap"]] + [v for v in c["sma"].values() if math.isfinite(v)] + list(c["fib"].values())
    resistance_points = [p for _,p in c["highs"][-35:]] + [c["vwap"]] + list(c["fib"].values())

    support_clusters = [x for x in _cluster([p for p in support_points if p < price], tolerance) if x]
    resistance_clusters = [x for x in _cluster([p for p in resistance_points if p > price], tolerance) if x]

    def build(clusters, side):
        out = []
        for group in clusters:
            center = float(np.mean(group))
            score, touches, reasons = _score_level(df, center, side, c, tolerance)
            zone_half = max(tolerance * 0.55, atr * 0.12)
            out.append(Level(side, center-zone_half, center+zone_half, score, center, reasons, touches))
        if side == "support":
            out.sort(key=lambda x: (abs(price-x.center), -x.score))
        else:
            out.sort(key=lambda x: (abs(x.center-price), -x.score))
        # Deduplicate nearby levels
        dedup = []
        for lvl in out:
            if not any(abs(lvl.center-d.center) < tolerance*1.2 for d in dedup):
                dedup.append(lvl)
        return dedup[:3]

    supports = build(support_clusters, "support")
    resistances = build(resistance_clusters, "resistance")

    # Fallback ATR-based levels if sparse
    while len(supports) < 3:
        i = len(supports)+1
        center = max(price * 0.05, price - atr*(1.2*i))
        low = max(0.01, center-atr*.15)
        high = max(low, center+atr*.15)
        supports.append(Level("support", low, high, max(55,75-i*6), center, ["ATR fallback"], 0))
    while len(resistances) < 3:
        i = len(resistances)+1
        center = price + atr*(1.2*i)
        resistances.append(Level("resistance", center-atr*.15, center+atr*.15, max(55,75-i*6), center, ["ATR fallback"], 0))

    # Institutional zone = strongest nearby support among S1-S3
    buy = max(supports, key=lambda x: (x.score, -abs(price-x.center)))

    # Breakout validation: close beyond S1 by 0.15 ATR + volume > 20-bar avg
    last_vol = float(df["volume"].iloc[-1])
    vol_avg = float(df["volume"].tail(20).mean())
    s1 = supports[0]
    breakdown = bool(float(df["close"].iloc[-1]) < s1.low - atr*0.15 and last_vol > vol_avg*1.15)

    if price > s1.high:
        bias = "Neutral / Bullish"
        state = "Above S1"
    elif s1.low <= price <= s1.high:
        bias = "Testing Support"
        state = "Inside S1"
    else:
        bias = "Bearish / Breakdown Risk"
        state = "Below S1"

    def ser(lvl):
        return {
            "side": lvl.side, "low": round(lvl.low, 2), "high": round(lvl.high, 2),
            "center": round(lvl.center, 2), "score": lvl.score,
            "touches": lvl.touches, "reasons": lvl.reasons[:6]
        }

    return {
        "price": round(price,2),
        "bias": bias,
        "state": state,
        "atr14": round(atr,2),
        "sma20": round(c["sma"][20],2) if math.isfinite(c["sma"][20]) else None,
        "sma50": round(c["sma"][50],2) if math.isfinite(c["sma"][50]) else None,
        "sma100": round(c["sma"][100],2) if math.isfinite(c["sma"][100]) else None,
        "sma200": round(c["sma"][200],2) if math.isfinite(c["sma"][200]) else None,
        "vwap": round(c["vwap"],2),
        "anchored_vwap": round(c["avwap"],2),
        "volume_poc": round(c["poc"],2),
        "fib": {k:round(v,2) for k,v in c["fib"].items()},
        "supports": [ser(x) for x in supports],
        "resistances": [ser(x) for x in resistances],
        "institutional_buy_zone": ser(buy),
        "breakout_validation": {
            "s1_breakdown_confirmed": breakdown,
            "last_volume": round(last_vol,2),
            "volume_20_avg": round(vol_avg,2),
            "rule": "Close below S1 by >= 0.15 ATR and volume > 1.15x 20-bar average"
        }
    }