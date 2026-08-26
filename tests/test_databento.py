import asyncio
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.income import databento
from app.income.databento import (
    build_expiration_list,
    classify_data_status,
    extract_bbo,
    latest_open_interest,
    nearest_expiration,
    normalize_contract,
    parse_definition_records,
    parse_px,
    resolve_mid,
    spread_percent,
    unavailable_payload,
)
from app.main import app


DEFINITIONS = [
    {
        "raw_symbol": "META  260831C00180000",
        "instrument_class": "C",
        "strike_price": 180,
        "expiration": "2026-08-31T00:00:00.000000000Z",
        "security_update_action": "A",
    },
    {
        "raw_symbol": "META  260831P00180000",
        "instrument_class": "P",
        "strike_price": 180,
        "expiration": "2026-08-31T00:00:00.000000000Z",
        "security_update_action": "A",
    },
    {
        "raw_symbol": "META  260908C00180000",
        "instrument_class": "C",
        "strike_price": 180,
        "expiration": "2026-09-08T00:00:00.000000000Z",
        "security_update_action": "A",
    },
    {
        "raw_symbol": "META",
        "instrument_class": "K",
        "strike_price": None,
        "expiration": None,
    },
]


class DatabentoNormalizeTests(unittest.TestCase):
    def test_spread_percent_example(self):
        self.assertEqual(spread_percent(3.81, 3.84, 3.825), 0.78)

    def test_spread_percent_invalid(self):
        self.assertIsNone(spread_percent(None, 3.84))
        self.assertIsNone(spread_percent(0, 3.84))

    def test_mid_from_bid_ask(self):
        self.assertEqual(resolve_mid(3.81, 3.84, None), 3.825)

    def test_sentinel_price_is_missing(self):
        self.assertIsNone(parse_px(9223372036854775807))
        bid, ask = extract_bbo({"bid_px_00": 9223372036854775807, "ask_px_00": 3.84})
        self.assertIsNone(bid)
        self.assertEqual(ask, 3.84)

    def test_json_levels_bbo(self):
        bid, ask = extract_bbo({"levels": [{"bid_px": 3.81, "ask_px": 3.84}]})
        self.assertEqual(bid, 3.81)
        self.assertEqual(ask, 3.84)

    def test_nearest_expiration_picks_closest(self):
        expirations = [
            {"expiration": "2026-08-31", "dte": 6},
            {"expiration": "2026-09-02", "dte": 8},
            {"expiration": "2026-09-08", "dte": 14},
        ]
        chosen = nearest_expiration(expirations, 7)
        self.assertEqual(chosen["dte"], 6)

    def test_past_expirations_are_dropped(self):
        built = build_expiration_list(
            [date(2026, 8, 1), date(2026, 9, 1)],
            today=date(2026, 8, 25),
        )
        self.assertEqual(built, [{"expiration": "2026-09-01", "dte": 7}])

    def test_definitions_keep_calls_and_puts(self):
        instruments = parse_definition_records(DEFINITIONS)
        self.assertEqual(len(instruments), 3)
        call = instruments["META 260831C00180000"]
        put = instruments["META 260831P00180000"]
        self.assertEqual(call["type"], "CALL")
        self.assertEqual(put["type"], "PUT")
        self.assertEqual(call["strike"], 180)
        self.assertEqual(call["expiration"], "2026-08-31")

    def test_greeks_are_not_invented(self):
        parsed = normalize_contract(
            {
                "symbol": "META 260831C00180000",
                "type": "CALL",
                "strike": 180,
                "expiration": "2026-08-31",
                "expiration_date": date(2026, 8, 31),
            },
            quote={"bid": 3.81, "ask": 3.84},
            open_interest=13683,
            volume=210,
            today=date(2026, 8, 25),
        )
        self.assertIsNone(parsed["delta"])
        self.assertIsNone(parsed["impliedVolatility"])
        self.assertEqual(parsed["bid"], 3.81)
        self.assertEqual(parsed["ask"], 3.84)
        self.assertEqual(parsed["mid"], 3.825)
        self.assertEqual(parsed["spreadPercent"], 0.78)
        self.assertEqual(parsed["openInterest"], 13683)
        self.assertEqual(parsed["dte"], 6)

    def test_open_interest_stat_type(self):
        interest = latest_open_interest([
            {"symbol": "META 260831C00180000", "stat_type": 9, "quantity": 13683},
            {"symbol": "META 260831C00180000", "stat_type": 6, "quantity": 99},
        ])
        self.assertEqual(interest["META 260831C00180000"], 13683)

    def test_status_never_live(self):
        now = datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(classify_data_status(now, today=date(2026, 8, 25)), "delayed")
        old = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(classify_data_status(old, today=date(2026, 8, 25)), "historical")
        self.assertNotEqual(classify_data_status(now, today=date(2026, 8, 25)), "realtime")

    def test_loads_only_nearest_expiration_quotes(self):
        databento._DEFINITIONS_CACHE.clear()
        databento._RANGE_CACHE.clear()
        calls = []

        async def fake_get(_client, path, params):
            return {
                "end": "2026-08-22T20:00:00Z",
                "schema": {"cbbo-1m": {"end": "2026-08-22T20:00:00Z"}},
            }

        async def fake_request(_client, path, params, allow_empty=False):
            calls.append(dict(params))
            schema = params.get("schema")
            if schema == "definition":
                return DEFINITIONS
            if schema == "cbbo-1m":
                symbols = params.get("symbols") or ""
                self.assertIn("260831", symbols)
                self.assertNotIn("260908", symbols)
                return [{
                    "symbol": "META  260831C00180000",
                    "ts_recv": "2026-08-22T19:59:00Z",
                    "levels": [{"bid_px": 3.81, "ask_px": 3.84}],
                }]
            if schema == "statistics":
                return [{"symbol": "META  260831C00180000", "stat_type": 9, "quantity": 13683}]
            if schema == "ohlcv-1d":
                return [{"symbol": "META  260831C00180000", "volume": 210}]
            return []

        async def run():
            with patch("app.income.databento._databento_get", new=fake_get), patch(
                "app.income.databento._databento_request", new=fake_request
            ):
                return await databento.get_option_chain_for_dte("META", 7, today=date(2026, 8, 25))

        result = asyncio.run(run())
        schemas = [item["schema"] for item in calls]
        self.assertIn("definition", schemas)
        self.assertIn("cbbo-1m", schemas)
        self.assertTrue(result["ok"])
        self.assertFalse(result["live"])
        self.assertEqual(result["dataStatus"], "historical")
        self.assertEqual(result["actualDte"], 6)
        self.assertEqual(result["expiration"], "2026-08-31")
        self.assertEqual(len(result["contracts"]), 2)
        call = [item for item in result["contracts"] if item["type"] == "CALL"][0]
        self.assertEqual(call["bid"], 3.81)
        self.assertIsNone(call["delta"])
        databento._DEFINITIONS_CACHE.clear()
        databento._RANGE_CACHE.clear()


class DatabentoRouteTests(unittest.TestCase):
    def setUp(self):
        databento._DEFINITIONS_CACHE.clear()
        databento._RANGE_CACHE.clear()
        self.client = TestClient(app)

        def fake_user():
            return type("U", (), {"id": 1, "email": "test@example.com"})()

        from app.auth import get_current_user
        app.dependency_overrides[get_current_user] = fake_user

    def tearDown(self):
        app.dependency_overrides.clear()
        databento._DEFINITIONS_CACHE.clear()
        databento._RANGE_CACHE.clear()

    def test_requires_auth(self):
        app.dependency_overrides.clear()
        r = self.client.get("/api/options/META?dte=7")
        self.assertEqual(r.status_code, 401)

    def test_invalid_ticker(self):
        r = self.client.get("/api/options/META!?dte=7")
        self.assertEqual(r.status_code, 400)

    def test_payload_from_databento(self):
        payload = {
            "ticker": "META",
            "requestedDte": 7,
            "actualDte": 6,
            "expiration": "2026-08-31",
            "updated": "2026-08-22 15:59:00 EDT",
            "live": False,
            "ok": True,
            "dataStatus": "historical",
            "expirations": [{"expiration": "2026-08-31", "dte": 6}],
            "contracts": [{
                "symbol": "META 260831C00180000",
                "type": "CALL",
                "strike": 180,
                "bid": 3.81,
                "ask": 3.84,
                "mid": 3.825,
                "delta": None,
                "impliedVolatility": None,
                "openInterest": 13683,
                "expiration": "2026-08-31",
                "dte": 6,
                "spreadPercent": 0.78,
                "volume": 210,
            }],
        }
        with patch("app.income.options_routes.get_option_chain_for_dte", new=AsyncMock(return_value=payload)):
            r = self.client.get("/api/options/META?dte=7")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["dataStatus"], "historical")
        self.assertFalse(body["live"])

    def test_unavailable_when_provider_missing(self):
        with patch(
            "app.income.options_routes.get_option_chain_for_dte",
            new=AsyncMock(return_value=unavailable_payload("META", 7)),
        ):
            r = self.client.get("/api/options/META?dte=7")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["contracts"], [])


if __name__ == "__main__":
    unittest.main()
