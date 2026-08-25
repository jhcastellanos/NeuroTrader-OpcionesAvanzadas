# NeuroTrader Institutional Levels™ V4.2 Unified

This build merges the strongest parts of the two versions:

- **V3.1 QA:** real OHLCV backend, Polygon integration, validated ticker/timeframe input, institutional-level engine, automated tests.
- **V4 Multi-Asset:** fast watchlist workflow and change-any-ticker interface.
- **V4.2:** adds automatic demo fallback so the complete app works immediately even before a Polygon key is configured.

## Run

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`.

### Demo mode
Leave `POLYGON_API_KEY` blank and `DEMO_MODE=true`. You can change among SNDK, NVDA, META, AVGO, MSFT, AAPL, AMD, TSLA, QQQ, SPY, or type another valid ticker.

### Live mode
Set `POLYGON_API_KEY=...`. The backend automatically prefers Polygon live/historical OHLCV and the API key never reaches the browser.

## QA

```bash
python -m unittest discover -s tests -v
```

The Volume POC remains an OHLCV bar-volume approximation, not trade-by-trade exchange volume profile.
