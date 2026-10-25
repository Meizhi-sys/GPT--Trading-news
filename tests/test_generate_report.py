from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.generate_report import build_site, run, target_session, validate_session


class CalendarTests(unittest.TestCase):
    def test_beijing_saturday_follows_friday_session(self):
        self.assertEqual(target_session(datetime.fromisoformat("2026-10-24T00:30:00+00:00")), "2026-10-23")

    def test_weekend_and_christmas_are_skipped(self):
        for dt in ["2026-10-25T00:30:00+00:00", "2026-12-26T00:30:00+00:00"]:
            with self.subTest(dt=dt):
                self.assertIsNone(target_session(datetime.fromisoformat(dt)))

    def test_shortened_session_and_dst(self):
        for dt, session in [("2026-11-28T00:30:00+00:00", "2026-11-27"),
                            ("2026-03-10T00:30:00+00:00", "2026-03-09")]:
            self.assertEqual(target_session(datetime.fromisoformat(dt)), session)

    def test_force_never_uses_unfinished_session(self):
        now = datetime.fromisoformat("2026-10-23T15:00:00+00:00")
        self.assertEqual(target_session(now), "2026-10-22")
        self.assertEqual(target_session(now, force=True), "2026-10-22")

    def test_schedule_delayed_past_new_york_midnight_keeps_friday(self):
        self.assertEqual(target_session(datetime.fromisoformat("2026-10-24T05:30:00+00:00")), "2026-10-23")


class GenerationTests(unittest.TestCase):
    now = datetime.fromisoformat("2026-10-24T00:30:00+00:00")

    def markets(self):
        return {"as_of": self.now.isoformat(), "equities": [
            {"symbol": "MU", "price": 100, "previous_close": 99, "change_percent": 100 / 99,
             "volume": 1000000, "market_time": "2026-10-23T20:00:00+00:00",
             "source_url": "https://example.com/mu"}],
            "commodities": [{"symbol": "GC=F", "price": 2000, "market_time": self.now.isoformat(),
                             "source_url": "https://example.com/gold"}],
            "gainers": {"scope": "Test fixture only", "rows": [
                {"symbol": "TEST", "name": "Fixture", "price": 10, "change_percent": 10,
                 "market_time": "2026-10-23T20:00:00+00:00", "source_url": "https://example.com/stock"}]},
            "errors": []}

    def news(self):
        return {"items": [{"title": "测试新闻", "summary": "仅供单元测试", "relevance": "测试关联性",
                           "sources": [{"url": "https://example.com/news", "title": "测试来源"}]}], "errors": []}

    def test_removes_stale_quotes_and_sub_ten_rows(self):
        markets = self.markets()
        markets["equities"][0]["market_time"] = "2026-10-22T20:00:00+00:00"
        markets["gainers"]["rows"][0]["price"] = 9.99
        self.assertTrue(validate_session(markets, "2026-10-23"))
        self.assertFalse(markets["equities"])
        self.assertFalse(markets["gainers"]["rows"])

    def test_skip_does_not_contact_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("scripts.market_data.collect_market_data") as market:
                result = run(datetime.fromisoformat("2026-10-25T00:30:00+00:00"),
                             Path(directory) / "reports", Path(directory) / "site")
            self.assertFalse(result["generated"])
            market.assert_not_called()

    def test_generates_archive_downloads_and_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            reports, site = Path(directory) / "reports", Path(directory) / "site"
            with patch("scripts.market_data.collect_market_data", side_effect=lambda _: self.markets()), \
                 patch("scripts.news_analysis.collect_news", side_effect=lambda *a, **kw: self.news()):
                first = run(self.now, reports, site)
                second = run(self.now, reports, site)
            self.assertEqual(first["status"], "complete")
            self.assertEqual(second["status"], "complete")
            self.assertEqual(len(list(reports.glob("????-??-??.json"))), 1)
            for file in ["index.html", "report.md", "report.json", "reports/2026-10-24/index.html",
                         "reports/2026-10-24/report.md", "reports/2026-10-24/report.json"]:
                self.assertTrue((site / file).is_file(), file)
            self.assertIn("2026-10-24", (site / "index.html").read_text())

    def test_failed_sources_produce_visible_failure_without_fake_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            reports, site = Path(directory) / "reports", Path(directory) / "site"
            with patch("scripts.market_data.collect_market_data", return_value={"errors": ["HTTP 403"]}), \
                 patch("scripts.news_analysis.collect_news", return_value={"items": [], "errors": ["缺少密钥"]}):
                result = run(self.now, reports, site)
            self.assertEqual(result["status"], "failed")
            stored = json.loads((site / "report.json").read_text())
            self.assertFalse(stored["market_data"].get("equities"))
            self.assertIn("HTTP 403", (site / "index.html").read_text())

    def test_rejects_misdated_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory) / "reports"
            reports.mkdir()
            (reports / "2026-10-24.json").write_text('{"report_date":"2026-10-23"}')
            with self.assertRaises(ValueError):
                build_site(reports, Path(directory) / "site")

    def test_failed_then_partial_retry_cannot_destroy_complete_report(self):
        with tempfile.TemporaryDirectory() as directory:
            reports, site = Path(directory) / "reports", Path(directory) / "site"
            with patch("scripts.market_data.collect_market_data", side_effect=lambda _: self.markets()), \
                 patch("scripts.news_analysis.collect_news", side_effect=lambda *a, **kw: self.news()):
                run(self.now, reports, site)
            with patch("scripts.market_data.collect_market_data", return_value={"errors": ["offline"]}), \
                 patch("scripts.news_analysis.collect_news", return_value={"items": [], "errors": ["offline"]}):
                failed = run(self.now, reports, site)
            with patch("scripts.market_data.collect_market_data", side_effect=lambda _: self.markets()), \
                 patch("scripts.news_analysis.collect_news", return_value={"items": [], "errors": ["offline"]}):
                partial = run(self.now, reports, site)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(partial["status"], "partial")
            retained = json.loads((site / "report.json").read_text())
            self.assertEqual(retained["status"], "complete")
            self.assertEqual(retained["news"]["items"][0]["title"], "测试新闻")
            self.assertEqual(retained["latest_attempt"]["status"], "partial")
            self.assertEqual(retained["last_success_at"], self.now.isoformat())


if __name__ == "__main__":
    unittest.main()
