# NeuroTrader Institutional Levels™ V4.2 Unified

This build merges the strongest parts of the two versions:

- **V3.1 QA:** real OHLCV backend, Polygon integration, validated ticker/timeframe input, institutional-level engine, automated tests.
- **V4 Multi-Asset:** selectable ticker combobox (SNDK, NVDA, META, AVGO, MSFT, AAPL, AMD, TSLA, QQQ, SPY).
- **V4.2:** Polygon.io live OHLCV by default. Demo bars stay available only if `DEMO_MODE=true`.

## Run

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Put your Polygon key in `.env`:

```
POLYGON_API_KEY=your_key_here
DEMO_MODE=false
```

```bash
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`, choose a ticker, then analyze. The API key never reaches the browser.

## QA

```bash
python -m unittest discover -s tests -v
```

The Volume POC remains an OHLCV bar-volume approximation, not trade-by-trade exchange volume profile.
