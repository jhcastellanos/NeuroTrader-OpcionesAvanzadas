import unittest
from datetime import datetime, timedelta, timezone

from app.income.demo_options import build_illustrative_chain
from app.income.engine import indicators as ind
from app.income.market import MarketSnapshot
from app.income.schemas import (
    Bar,
    BarsResponse,
    CashSecuredPutRequest,
    CorporateEventsResponse,
    CoveredCallRequest,
    DataSourceStatus,
    MarketSessionStatus,
    Quote,
)
from app.income.service import build_covered_call, build_csp, build_underlying, dashboard_from_snapshot
from app.income.explain import compare_from_snapshot, explanation_from_result
from app.income.schemas import CompareStrategiesRequest
import asyncio


def _bars(n=80, start=100.0):
    now = datetime.now(timezone.utc)
    rows = []
    price = start
    for i in range(n):
        price *= 1.002
        rows.append(Bar(
            timestamp_utc=now - timedelta(days=n - i),
            open=price * 0.99,
            high=price * 1.01,
            low=price * 0.98,
            close=price,
            volume=2_000_000,
        ))
    return rows


class IncomeEngineTests(unittest.TestCase):
    def test_sma_formula(self):
        self.assertEqual(ind.sma([1, 2, 3, 4, 5], 5), 3.0)
        self.assertIsNone(ind.sma([1, 2], 5))

    def test_illustrative_chain_uses_real_spot(self):
        chain = build_illustrative_chain("AAPL", 200.0)
        self.assertTrue(chain.contracts)
        self.assertTrue(all(c.underlying_ticker == "AAPL" for c in chain.contracts))
        strikes = [c.strike for c in chain.contracts]
        self.assertTrue(min(strikes) < 200.0 < max(strikes))

    def test_covered_call_and_csp_from_snapshot(self):
        price = 150.0
        bars = _bars(120, price * 0.8)
        quote = Quote(
            ticker="MSFT",
            price=price,
            change=1.2,
            change_percent=0.8,
            volume=8_000_000,
            day_high=price * 1.01,
            day_low=price * 0.99,
            week52_high=price * 1.3,
            week52_low=price * 0.7,
            market_session=MarketSessionStatus.CLOSED,
            data_source_status=DataSourceStatus.LIVE,
            updated_at_utc=datetime.now(timezone.utc),
            updated_at_ny="2026-08-25 16:00:00 EDT",
            is_demo=False,
        )
        snapshot = MarketSnapshot(
            quote=quote,
            bars=BarsResponse(
                ticker="MSFT",
                timeframe="1d",
                bars=bars,
                data_source_status=DataSourceStatus.LIVE,
                is_demo=False,
                updated_at_ny=quote.updated_at_ny,
            ),
            events=CorporateEventsResponse(
                ticker="MSFT",
                earnings_available=False,
                earnings_note="test",
                data_source_status=DataSourceStatus.DEMO,
                is_demo=True,
                updated_at_ny=quote.updated_at_ny,
            ),
            chain=build_illustrative_chain("MSFT", price),
            options_live=False,
        )
        underlying = build_underlying(snapshot)
        self.assertEqual(underlying.ticker, "MSFT")
        self.assertGreaterEqual(underlying.score.total_score, 0)
        cc = build_covered_call(snapshot, CoveredCallRequest(ticker="MSFT", shares_owned=100, cost_basis=price))
        csp = build_csp(snapshot, CashSecuredPutRequest(ticker="MSFT", capital_available=price * 100))
        self.assertEqual(cc.strategy, "covered_call")
        self.assertEqual(csp.strategy, "cash_secured_put")
        self.assertIn(cc.status, {"oportunidad_valida", "aceptable_con_precaucion", "esperar", "no_aplica"})

    def test_dashboard_payload_has_chain_not_auto_strategy(self):
        price = 150.0
        bars = _bars(120, price * 0.8)
        quote = Quote(
            ticker="MSFT",
            price=price,
            change=1.2,
            change_percent=0.8,
            volume=8_000_000,
            relative_volume=1.1,
            day_high=price * 1.01,
            day_low=price * 0.99,
            week52_high=price * 1.3,
            week52_low=price * 0.7,
            market_session=MarketSessionStatus.CLOSED,
            data_source_status=DataSourceStatus.LIVE,
            updated_at_utc=datetime.now(timezone.utc),
            updated_at_ny="2026-08-25 16:00:00 EDT",
            is_demo=False,
        )
        snapshot = MarketSnapshot(
            quote=quote,
            bars=BarsResponse(
                ticker="MSFT",
                timeframe="1d",
                bars=bars,
                data_source_status=DataSourceStatus.LIVE,
                is_demo=False,
                updated_at_ny=quote.updated_at_ny,
            ),
            events=CorporateEventsResponse(
                ticker="MSFT",
                earnings_available=False,
                earnings_note="test",
                data_source_status=DataSourceStatus.DEMO,
                is_demo=True,
                updated_at_ny=quote.updated_at_ny,
            ),
            chain=build_illustrative_chain("MSFT", price),
            options_live=False,
        )
        payload = dashboard_from_snapshot(snapshot)
        self.assertEqual(payload["ticker"], "MSFT")
        self.assertIn("quote", payload)
        self.assertIn("underlying", payload)
        self.assertIn("chain", payload)
        self.assertNotIn("covered_call", payload)
        self.assertNotIn("csp", payload)
        self.assertTrue(payload["chain"]["contracts"])
        self.assertIn("trend_score", payload["underlying"]["score"])

    def test_template_explanation_and_compare(self):
        price = 150.0
        bars = _bars(120, price * 0.8)
        quote = Quote(
            ticker="MSFT",
            price=price,
            change=1.2,
            change_percent=0.8,
            volume=8_000_000,
            day_high=price * 1.01,
            day_low=price * 0.99,
            week52_high=price * 1.3,
            week52_low=price * 0.7,
            market_session=MarketSessionStatus.CLOSED,
            data_source_status=DataSourceStatus.LIVE,
            updated_at_utc=datetime.now(timezone.utc),
            updated_at_ny="2026-08-25 16:00:00 EDT",
            is_demo=False,
        )
        snapshot = MarketSnapshot(
            quote=quote,
            bars=BarsResponse(
                ticker="MSFT",
                timeframe="1d",
                bars=bars,
                data_source_status=DataSourceStatus.LIVE,
                is_demo=False,
                updated_at_ny=quote.updated_at_ny,
            ),
            events=CorporateEventsResponse(
                ticker="MSFT",
                earnings_available=False,
                earnings_note="test",
                data_source_status=DataSourceStatus.DEMO,
                is_demo=True,
                updated_at_ny=quote.updated_at_ny,
            ),
            chain=build_illustrative_chain("MSFT", price),
            options_live=False,
        )
        cc = build_covered_call(snapshot, CoveredCallRequest(ticker="MSFT", shares_owned=100, cost_basis=price))
        explanation = asyncio.run(explanation_from_result(cc))
        self.assertIn("MSFT", explanation.explanation)
        self.assertEqual(explanation.provider, "template")
        self.assertIn("no es asesoramiento financiero", explanation.explanation.lower())

        compare = asyncio.run(
            compare_from_snapshot(
                snapshot,
                CompareStrategiesRequest(
                    ticker="MSFT",
                    shares_owned=100,
                    cost_basis=price,
                    capital_available=price * 100,
                ),
            )
        )
        self.assertEqual(compare.ticker, "MSFT")
        self.assertIsNotNone(compare.covered_call)
        self.assertIsNotNone(compare.csp)
        self.assertTrue(compare.ai_comparison)


if __name__ == "__main__":
    unittest.main()
