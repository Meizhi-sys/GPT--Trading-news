"""Market data tests are offline fixtures; no prices are claimed as real."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import market_data as md


NOW = datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)
MARKET = datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc)


def quote_record(symbol="TEST", price=20, previous=10, **kwargs):
    item = {
        "symbol": symbol, "quoteType": "EQUITY", "exchange": "NMS",
        "regularMarketPrice": price, "regularMarketPreviousClose": previous,
        "regularMarketVolume": 1000, "regularMarketTime": int(MARKET.timestamp()),
        "currency": "USD", "marketCap": 2_000_000_000,
    }
    item.update(kwargs)
    return item


def gainers_payload(items):
    return {"finance": {"error": None, "result": [{"quotes": items}]}}


def chart_payload(symbol="MU"):
    return {"chart": {"error": None, "result": [{
        "meta": {
            "symbol": symbol, "regularMarketPrice": 22,
            "regularMarketTime": int(MARKET.timestamp()),
            "exchangeTimezoneName": "America/New_York", "currency": "USD",
            "previousClose": 3, "chartPreviousClose": 2,
        },
        "timestamp": [int((MARKET - timedelta(days=offset, hours=6)).timestamp()) for offset in (2, 1, 0)],
        "indicators": {"quote": [{"close": [18, 20, 22], "volume": [500, 600, 700]}]},
    }]}}


class GainersTests(unittest.TestCase):
    def test_filters_and_computed_order(self):
        items = [
            quote_record("LOW", 9.99, 1),
            quote_record("ETF", quoteType="ETF"),
            quote_record("OTC", exchange="PNK"),
            quote_record("WRONG_CURRENCY", currency="CAD"),
            quote_record("TEN", 10, 8, regularMarketChangePercent=999),
            quote_record("BEST", 30, 20),
            quote_record("FLAT", 10, 10),
        ]
        result = md._read_gainers(gainers_payload(items), NOW)
        self.assertEqual([row["symbol"] for row in result["rows"]], ["BEST", "TEN"])
        self.assertAlmostEqual(result["rows"][1]["change_percent"], 25)
        self.assertIn("样本", result["scope"])
        self.assertEqual(result["rows"][0]["estimated_turnover_usd"], 30_000)

    def test_rejects_invalid_volume_price_previous_and_future(self):
        items = [
            quote_record("BAD_VOLUME", regularMarketVolume=-1),
            quote_record("BAD_PRICE", float("nan")),
            quote_record("BAD_PREVIOUS", previous=0),
            quote_record("BAD_TIME", regularMarketTime=int((NOW + timedelta(hours=1)).timestamp())),
            quote_record("STALE", regularMarketTime=int((NOW - timedelta(days=6)).timestamp())),
            quote_record("MISSING", regularMarketTime=None),
        ]
        result = md._read_gainers(gainers_payload(items), NOW)
        self.assertEqual(result["rows"], [])
        self.assertTrue(any("6 条" in message for message in result["limitations"]))

    def test_never_mixes_dates_and_deduplicates(self):
        old_time = int((MARKET - timedelta(days=1)).timestamp())
        result = md._read_gainers(gainers_payload([
            quote_record("OLD", 200, 1, regularMarketTime=old_time),
            quote_record("NEW", 20, 10), quote_record("NEW", 20, 10),
        ]), NOW)
        self.assertEqual([row["symbol"] for row in result["rows"]], ["NEW"])
        self.assertEqual(result["rows"][0]["market_date"], "2026-10-23")

    def test_ten_only_and_sample_limited_to_250(self):
        items = [quote_record(f"STOCK{i}", 20 + i, 10) for i in range(251)]
        result = md._read_gainers(gainers_payload(items), NOW)
        self.assertEqual(len(result["rows"]), 10)
        self.assertEqual(result["rows"][0]["symbol"], "STOCK249")


class ChartTests(unittest.TestCase):
    def test_previous_close_from_adjacent_history_not_range_start(self):
        result = md._read_chart(chart_payload(), "MU", NOW)
        self.assertEqual(result["previous_close"], 20)
        self.assertAlmostEqual(result["change_percent"], 10)
        self.assertEqual(result["market_date"], "2026-10-23")
        self.assertEqual(result["volume"], 700)
        self.assertEqual(len(result["history"]), 3)

    def test_prefers_explicit_regular_previous_close(self):
        payload = chart_payload()
        payload["chart"]["result"][0]["meta"]["regularMarketPreviousClose"] = 21
        result = md._read_chart(payload, "MU", NOW)
        self.assertEqual(result["previous_close"], 21)

    def test_missing_date_mismatched_symbol_or_bar_lengths_rejected(self):
        payload = chart_payload()
        payload["chart"]["result"][0]["meta"].pop("regularMarketTime")
        with self.assertRaises(md.DataUnavailable):
            md._read_chart(payload, "MU", NOW)
        with self.assertRaises(md.DataUnavailable):
            md._read_chart(chart_payload(), "MRNA", NOW)
        payload = chart_payload()
        payload["chart"]["result"][0]["indicators"]["quote"][0]["close"].pop()
        with self.assertRaises(md.DataUnavailable):
            md._read_chart(payload, "MU", NOW)

    def test_futures_carry_warning_and_no_assumed_contract_month(self):
        result = md._read_chart(chart_payload("GC=F"), "GC=F", NOW)
        self.assertEqual(result["instrument_type"], "futures")
        self.assertIsNone(result["contract_month"])
        self.assertIn(md.FUTURES_WARNING, result["limitations"])

    def test_non_usd_and_malformed_nested_payload_rejected(self):
        payload = chart_payload()
        payload["chart"]["result"][0]["meta"]["currency"] = "CAD"
        with self.assertRaises(md.DataUnavailable):
            md._read_chart(payload, "MU", NOW)
        with self.assertRaises(md.DataUnavailable):
            md._read_chart({"chart": None}, "MU", NOW)
        with self.assertRaises(md.DataUnavailable):
            md._read_gainers({"finance": None}, NOW)


class CollectionTests(unittest.TestCase):
    def test_requires_aware_cutoff(self):
        with self.assertRaises(ValueError):
            md.collect_market_data(datetime(2026, 10, 24))

    def test_aware_cutoff_is_normalized_to_utc(self):
        shanghai_now = NOW.astimezone(timezone(timedelta(hours=8)))
        with patch.object(md, "_request_json", side_effect=md.DataUnavailable("offline fixture")):
            result = md.collect_market_data(shanghai_now)
        self.assertEqual(result["as_of"], NOW.isoformat())

    def test_invalid_exchange_timezone_rejected(self):
        payload = chart_payload()
        payload["chart"]["result"][0]["meta"]["exchangeTimezoneName"] = "Invalid/Timezone"
        with self.assertRaises(md.DataUnavailable):
            md._read_chart(payload, "MU", NOW)

    def test_independent_errors_are_explicit_not_invented(self):
        with patch.object(md, "_request_json", side_effect=md.DataUnavailable("数据源 HTTP 403")) as fetch:
            result = md.collect_market_data(NOW)
        self.assertEqual(len(result["errors"]), 8)
        self.assertTrue(all(isinstance(error, str) for error in result["errors"]))
        self.assertEqual(result["equities"], [])
        self.assertEqual(result["commodities"], [])
        self.assertEqual(result["gainers"]["rows"], [])
        self.assertEqual(fetch.call_count, 8)

    def test_successful_collection(self):
        def fixture(url):
            if "screener" in url:
                return gainers_payload([quote_record()])
            from urllib.parse import unquote
            symbol = unquote(url.split("/chart/")[1].split("?")[0])
            return chart_payload(symbol)
        with patch.object(md, "_request_json", side_effect=fixture):
            result = md.collect_market_data(NOW)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["equities"]), 5)
        self.assertEqual(len(result["commodities"]), 2)

    def test_http_403_not_retried(self):
        failure = HTTPError("https://example.invalid", 403, "Forbidden", {}, None)
        with patch.object(md, "urlopen", side_effect=failure) as fetch:
            with self.assertRaisesRegex(md.DataUnavailable, "HTTP 403"):
                md._request_json("https://example.invalid")
        self.assertEqual(fetch.call_count, 1)

    def test_transient_http_retries_bounded(self):
        failure = HTTPError("https://example.invalid", 503, "Unavailable", {}, None)
        with patch.object(md, "urlopen", side_effect=failure) as fetch, patch.object(md.time, "sleep"):
            with self.assertRaisesRegex(md.DataUnavailable, "HTTP 503"):
                md._request_json("https://example.invalid")
        self.assertEqual(fetch.call_count, 3)


if __name__ == "__main__":
    unittest.main()
