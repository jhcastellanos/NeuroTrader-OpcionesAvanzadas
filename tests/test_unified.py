import unittest
from fastapi.testclient import TestClient
from app.main import WATCHLIST, _demo_ohlcv, app
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


class ApiSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_tickers_combobox_source(self):
        r = self.client.get("/api/tickers")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tickers"], WATCHLIST)

    def test_home_has_ticker_combobox_without_default(self):
        r = self.client.get("/")
        html = r.text
        self.assertIn('id="ticker"', html)
        self.assertIn('list="tickerList"', html)
        self.assertIn("Seleccionar o escribir ticker", html)
        self.assertIn("Premium Income", html)
        self.assertIn('id="tabIncome"', html)
        self.assertIn("Evaluar Covered Call", html)
        self.assertIn("Underlying Score", html)
        self.assertIn("Comparar con IA", html)
        self.assertIn("/static/premium.js", html)
        self.assertNotIn('value="SNDK"', html)
        self.assertNotIn('id="watch"', html)


if __name__ == "__main__":
    unittest.main()
