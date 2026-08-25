import unittest
import numpy as np
import pandas as pd

from app.engine import calculate_levels

def synthetic_ohlcv(n=500, seed=7):
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.18, 2.2, n)
    close = 100 + np.cumsum(steps)
    close = np.maximum(close, 5)
    open_ = close + rng.normal(0, 0.8, n)
    high = np.maximum(open_, close) + rng.uniform(0.4, 2.0, n)
    low = np.minimum(open_, close) - rng.uniform(0.4, 2.0, n)
    low = np.maximum(low, 0.5)
    volume = rng.integers(500_000, 5_000_000, n)
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close, "volume": volume
    })

class EngineTests(unittest.TestCase):
    def test_full_output(self):
        out = calculate_levels(synthetic_ohlcv())
        self.assertEqual(len(out["supports"]), 3)
        self.assertEqual(len(out["resistances"]), 3)
        self.assertGreater(out["price"], 0)
        self.assertGreater(out["atr14"], 0)
        self.assertIsNotNone(out["sma200"])
        for lvl in out["supports"] + out["resistances"]:
            self.assertGreater(lvl["low"], 0)
            self.assertGreaterEqual(lvl["high"], lvl["low"])
            self.assertTrue(0 <= lvl["score"] <= 100)

    def test_short_but_valid_history(self):
        out = calculate_levels(synthetic_ohlcv(120))
        self.assertIsNone(out["sma200"])
        self.assertEqual(len(out["supports"]), 3)
        self.assertEqual(len(out["resistances"]), 3)

    def test_low_price_asset_never_negative(self):
        df = synthetic_ohlcv(120)
        scale = 0.03
        for c in ["open","high","low","close"]:
            df[c] *= scale
        out = calculate_levels(df)
        self.assertTrue(all(x["low"] > 0 for x in out["supports"]))

if __name__ == "__main__":
    unittest.main()