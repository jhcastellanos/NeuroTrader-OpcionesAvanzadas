import unittest
from app.data_provider import normalize_ticker, normalize_timeframe

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

if __name__ == "__main__":
    unittest.main()