import os
from pathlib import Path
import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from .data_provider import fetch_polygon_ohlcv, normalize_ticker, normalize_timeframe
from .engine import calculate_levels

load_dotenv()
app = FastAPI(title="NeuroTrader Institutional Levels", version="4.2.0")
STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

DEMO_PRICES = {
    "SNDK": 1596.0, "NVDA": 180.0, "META": 558.0, "AVGO": 340.0,
    "MSFT": 520.0, "AAPL": 230.0, "AMD": 190.0, "TSLA": 355.0,
    "QQQ": 610.0, "SPY": 695.0,
}

def _demo_ohlcv(symbol: str, timeframe: str, n: int = 500) -> pd.DataFrame:
    """Deterministic synthetic OHLCV so the full app works without an API key."""
    seed = sum((i + 1) * ord(c) for i, c in enumerate(symbol + timeframe)) % (2**32 - 1)
    rng = np.random.default_rng(seed)
    target = DEMO_PRICES.get(symbol, float(75 + (seed % 425)))
    drift = 0.00025 + (seed % 7) * 0.000035
    sigma = 0.012 + (seed % 9) * 0.001
    ret = rng.normal(drift, sigma, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    close *= target / close[-1]
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.0035, n))
    spread = np.clip(np.abs(rng.normal(0.007, 0.0025, n)), 0.002, 0.025)
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    low = np.maximum(low, 0.01)
    volume = rng.integers(700_000, 15_000_000, n)
    freq = "h" if timeframe in ("1h", "4h", "hour") else ("7D" if timeframe in ("week", "weekly") else "D")
    ts = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n, freq=freq)
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume})

@app.get("/")
async def home():
    return FileResponse(STATIC / "index.html")

@app.get("/api/health")
async def health():
    live = bool(os.getenv("POLYGON_API_KEY", "").strip())
    demo_enabled = os.getenv("DEMO_MODE", "true").lower() in {"1", "true", "yes", "on"}
    return {
        "ok": True,
        "version": "4.2.0",
        "provider": "polygon" if live else "demo",
        "polygon_configured": live,
        "demo_mode": (not live and demo_enabled),
        "market_data_ready": live or demo_enabled,
    }

@app.get("/api/levels/{ticker}")
async def levels(
    ticker: str,
    timeframe: str = Query("day"),
    limit: int = Query(500, ge=100, le=2000),
):
    try:
        symbol = normalize_ticker(ticker)
        tf = normalize_timeframe(timeframe)
        live = bool(os.getenv("POLYGON_API_KEY", "").strip())
        demo_enabled = os.getenv("DEMO_MODE", "true").lower() in {"1", "true", "yes", "on"}
        if live:
            df = await fetch_polygon_ohlcv(symbol, timeframe=tf, limit=limit)
            source = "Polygon Live"
        elif demo_enabled:
            df = _demo_ohlcv(symbol, tf, limit)
            source = "Demo OHLCV"
        else:
            raise RuntimeError("No market-data source configured. Add POLYGON_API_KEY or enable DEMO_MODE=true.")

        result = calculate_levels(df)
        result.update({
            "ticker": symbol,
            "timeframe": tf,
            "bars": len(df),
            "last_bar_time": df["timestamp"].iloc[-1].isoformat(),
            "engine_version": "4.2.0",
            "data_source": source,
        })
        return result
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Market-data provider HTTP error: {e.response.status_code}")
    except Exception:
        raise HTTPException(status_code=500, detail="Unexpected server error.")
