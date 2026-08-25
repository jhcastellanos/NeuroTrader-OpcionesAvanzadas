import unittest

import pandas as pd

from app.data_provider import (
    apply_live_snapshot,
    apply_session_bar,
    live_quote_from_cnbc,
    lookback_start,
    normalize_ticker,
    normalize_timeframe,
    session_bar_from_minutes,
)


class ProviderValidationTests(unittest.TestCase):
    def test_ticker(self):
        self.assertEqual(normalize_ticker("sndk"), "SNDK")
        self.assertEqual(normalize_ticker("BRK.B"), "BRK.B")
        with self.assertRaises(ValueError):
            normalize_ticker("<script>")

    def test_timeframe(self):
        self.assertEqual(normalize_timeframe("4H"), "4h")
        with self.assertRaises(ValueError):
            normalize_timeframe("2day")

    def test_lookback_start_is_before_today(self):
        start = lookback_start("day", 500)
        self.assertRegex(start, r"^\d{4}-\d{2}-\d{2}$")
        self.assertLess(start, pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d"))

    def test_snapshot_appends_today_when_aggs_stop_yesterday(self):
        yesterday = pd.Timestamp("2026-08-24 20:00:00", tz="UTC")
        df = pd.DataFrame({
            "timestamp": [yesterday],
            "open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5],
            "volume": [1_000_000.0], "provider_vwap": [100.2],
        })
        now = pd.Timestamp("2026-08-25 15:45:00", tz="America/New_York")
        snap = {
            "status": "OK",
            "ticker": {
                "day": {"o": 101, "h": 103, "l": 100.5, "c": 102.4, "v": 2_000_000},
                "lastTrade": {"p": 102.55, "t": int(now.tz_convert("UTC").timestamp() * 1000)},
            },
        }
        out = apply_live_snapshot(df, snap, "day", now=now)
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(float(out.iloc[-1]["close"]), 102.55)
        last_et = pd.Timestamp(out.iloc[-1]["timestamp"]).tz_convert("America/New_York")
        self.assertEqual(str(last_et.date()), "2026-08-25")

    def test_snapshot_updates_existing_today_bar(self):
        now = pd.Timestamp("2026-08-25 15:45:00", tz="America/New_York")
        today = pd.Timestamp("2026-08-25 13:30:00", tz="America/New_York").tz_convert("UTC")
        df = pd.DataFrame({
            "timestamp": [today],
            "open": [101.0], "high": [102.0], "low": [100.0], "close": [101.5],
            "volume": [500_000.0],
        })
        snap = {
            "ticker": {
                "day": {"o": 101, "h": 104, "l": 99.5, "c": 103.1, "v": 3_000_000},
                "lastTrade": {"p": 103.2, "t": int(now.tz_convert("UTC").timestamp() * 1000)},
            }
        }
        out = apply_live_snapshot(df, snap, "day", now=now)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(float(out.iloc[-1]["close"]), 103.2)
        self.assertGreaterEqual(float(out.iloc[-1]["high"]), 104.0)

    def test_minutes_build_today_session_bar(self):
        now = pd.Timestamp("2026-08-25 16:00:00", tz="America/New_York")
        minutes = pd.DataFrame({
            "timestamp": [
                pd.Timestamp("2026-08-25 09:30:00", tz="America/New_York").tz_convert("UTC"),
                pd.Timestamp("2026-08-25 15:59:00", tz="America/New_York").tz_convert("UTC"),
            ],
            "open": [100.0, 102.0],
            "high": [101.0, 103.5],
            "low": [99.5, 101.8],
            "close": [100.8, 103.2],
            "volume": [1000.0, 2000.0],
        })
        yesterday = pd.Timestamp("2026-08-24 20:00:00", tz="UTC")
        df = pd.DataFrame({
            "timestamp": [yesterday],
            "open": [98.0], "high": [99.0], "low": [97.0], "close": [98.5],
            "volume": [5000.0],
        })
        bar = session_bar_from_minutes(minutes, now=now)
        out = apply_session_bar(df, bar, "day", now=now)
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(float(out.iloc[-1]["close"]), 103.2)
        last_et = pd.Timestamp(out.iloc[-1]["timestamp"]).tz_convert("America/New_York")
        self.assertEqual(str(last_et.date()), "2026-08-25")

    def test_cnbc_quote_builds_today_bar(self):
        payload = {
            "QuickQuoteResult": {
                "QuickQuote": [{
                    "symbol": "AAPL",
                    "last": "309.90",
                    "open": "310.79",
                    "high": "313.59",
                    "low": "308.21",
                    "volume": "22997120",
                    "last_time": "2026-08-25T16:00:00.000-0400",
                    "last_time_msec": "1787688000000",
                }]
            }
        }
        parsed = live_quote_from_cnbc(payload)
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed["bar"]["close"], 309.90)
        self.assertAlmostEqual(parsed["bar"]["open"], 310.79)
        last_et = pd.Timestamp(parsed["bar"]["timestamp"]).tz_convert("America/New_York")
        self.assertEqual(str(last_et.date()), "2026-08-25")


if __name__ == "__main__":
    unittest.main()
