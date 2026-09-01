import os
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from sqlalchemy import text

from .data_provider import fetch_polygon_ohlcv, normalize_ticker, normalize_timeframe, polygon_api_key
from .db import get_engine, init_db
from .engine import calculate_levels
from .income.options_routes import router as options_router
from .income.routes import router as income_router

load_dotenv(override=False)
logger = logging.getLogger("neurotrader")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        init_db()
    except Exception:
        logger.exception("Database init failed at startup")
    yield

app = FastAPI(title="NeuroTrader Institutional Levels", version="4.2.0", lifespan=lifespan)
STATIC = Path(__file__).resolve().parent / "static"
if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(income_router)
app.include_router(options_router)

WATCHLIST = ["SNDK", "NVDA", "META", "AVGO", "MSFT", "AAPL", "AMD", "TSLA", "QQQ", "SPY"]

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
    index = STATIC / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=500, detail="Frontend bundle missing.")
    return FileResponse(index)

def _polygon_configured() -> bool:
    return bool(polygon_api_key())


def _polygon_missing_message() -> str:
    if (os.getenv("VERCEL") or "").strip() == "1":
        return (
            "POLYGON_API_KEY no está en este deploy de Vercel. "
            "Añádela en Settings → Environment Variables del proyecto que sirve este dominio, "
            "entorno Production, y vuelve a desplegar."
        )
    return "Configura POLYGON_API_KEY en .env para usar datos reales de Polygon.io."


def _demo_enabled() -> bool:
    return os.getenv("DEMO_MODE", "false").lower() in {"1", "true", "yes", "on"}


@app.get("/api/health")
async def health():
    live = _polygon_configured()
    demo_enabled = _demo_enabled()
    db_ok = False
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "ok": True,
        "version": "4.2.0",
        "provider": "polygon" if live else ("demo" if demo_enabled else "none"),
        "polygon_configured": live,
        "demo_mode": (not live and demo_enabled),
        "market_data_ready": live or demo_enabled,
        "database": db_ok,
        "tickers": WATCHLIST,
    }


@app.get("/api/tickers")
async def tickers():
    return {"tickers": WATCHLIST}

@app.get("/api/levels/{ticker}")
async def levels(
    ticker: str,
    timeframe: str = Query("day"),
    limit: int = Query(500, ge=100, le=2000),
):
    try:
        symbol = normalize_ticker(ticker)
        tf = normalize_timeframe(timeframe)
        live = _polygon_configured()
        demo_enabled = _demo_enabled()
        if live:
            df = await fetch_polygon_ohlcv(symbol, timeframe=tf, limit=limit)
            source = "Polygon.io Live"
        elif demo_enabled:
            df = _demo_ohlcv(symbol, tf, limit)
            source = "Demo OHLCV"
        else:
            raise RuntimeError(_polygon_missing_message())

        result = calculate_levels(df)
        live_meta = dict(df.attrs.get("live") or {})
        if live_meta.get("session_applied"):
            if live_meta.get("quote_source"):
                source = "Cotización en vivo + histórico Polygon"
            else:
                source = "Polygon.io sesión de hoy"
        elif source.startswith("Polygon"):
            source = "Polygon.io último día cerrado"
        result.update({
            "ticker": symbol,
            "timeframe": tf,
            "bars": len(df),
            "last_bar_time": df["timestamp"].iloc[-1].isoformat(),
            "engine_version": "4.2.0",
            "data_source": source,
            "live": {
                "session_date": live_meta.get("session_date"),
                "session_applied": bool(live_meta.get("session_applied")),
                "quote_source": live_meta.get("quote_source"),
            },
        })
        return result
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Market-data provider HTTP error: {e.response.status_code}")
    except Exception:
        raise HTTPException(status_code=500, detail="Unexpected server error.")
