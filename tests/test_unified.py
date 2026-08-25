import unittest
from app.main import _demo_ohlcv
from app.engine import calculate_levels

class UnifiedDemoTests(unittest.TestCase):
    def test_demo_multi_asset(self):
        for symbol in ["SNDK","NVDA","META","QQQ","SPY","XYZ"]:
            df = _demo_ohlcv(symbol, "day", 500)
            out = calculate_levels(df)
            self.assertEqual(len(out["supports"]), 3)
            self.assertEqual(len(out["resistances"]), 3)
            self.assertGreater(out["price"], 0)

    def test_demo_timeframes(self):
        for tf in ["1h","4h","day","week"]:
            df = _demo_ohlcv("SNDK", tf, 300)
            self.assertEqual(len(df), 300)
            self.assertTrue(df["timestamp"].is_monotonic_increasing)

if __name__ == "__main__":
    unittest.main()
