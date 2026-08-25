import os
import re
import httpx
import pandas as pd

POLYGON_BASE = "https://api.polygon.io"

TF_MAP = {
    "minute": (1, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "30m": (30, "minute"),
    "hour": (1, "hour"),
    "1h": (1, "hour"),
    "4h": (4, "hour"),
    "day": (1, "day"),
    "daily": (1, "day"),
    "week": (1, "week"),
    "weekly": (1, "week"),
}

TICKER_RE = re.compile(r"^[A-Z0-9.^-]{1,15}$")

def normalize_timeframe(timeframe: str) -> str:
    tf = (timeframe or "day").lower().strip()
    if tf not in TF_MAP:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return tf

def normalize_ticker(ticker: str) -> str:
    symbol = (ticker or "").upper().strip()
    if not TICKER_RE.fullmatch(symbol):
        raise ValueError("Invalid ticker format.")
    return symbol

async def fetch_polygon_ohlcv(ticker: str, timeframe: str = "day", limit: int = 500) -> pd.DataFrame:
    api_key = os.getenv("POLYGON_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY is not configured.")

    symbol = normalize_ticker(ticker)
    tf = normalize_timeframe(timeframe)
    mult, span = TF_MAP[tf]

    # Request the most recent bars. Polygon sorts descending, then we reverse
    # locally so all indicator calculations run chronologically.
    from_date = "2020-01-01"
    to_date = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{symbol}/range/{mult}/{span}/{from_date}/{to_date}"
    requested_limit = max(100, min(int(limit), 50000))
    params = {
        "adjusted": "true",
        "sort": "desc",
        "limit": requested_limit,
        "apiKey": api_key,
    }

    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        payload = r.json()

    if payload.get("status") == "ERROR":
        raise ValueError(payload.get("error") or "Market-data provider returned an error.")

    results = payload.get("results") or []
    if not results:
        raise ValueError(f"No OHLCV data returned for {symbol}.")

    df = pd.DataFrame(results).rename(columns={
        "o": "open", "h": "high", "l": "low", "c": "close",
        "v": "volume", "t": "timestamp", "vw": "provider_vwap"
    })
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Provider response missing columns: {', '.join(missing)}")

    keep = [c for c in required + ["provider_vwap"] if c in df.columns]
    df = df[keep].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True, errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = (
        df.dropna(subset=required)
          .sort_values("timestamp")
          .tail(requested_limit)
          .reset_index(drop=True)
    )

    if len(df) < 30:
        raise ValueError("Not enough bars to calculate institutional levels.")
    if (df[["open","high","low","close"]] <= 0).any().any():
        raise ValueError("Invalid non-positive market prices returned by provider.")
    if (df["volume"] < 0).any():
        raise ValueError("Invalid negative volume returned by provider.")

    return df