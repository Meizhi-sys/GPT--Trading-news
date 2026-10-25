import unittest

from scripts.render_report import render_markdown, render_report


def sample_report():
    return {
        "schema_version": 1, "status": "partial", "report_date": "2026-10-24",
        "session_date": "2026-10-23", "generated_at": "2026-10-24T08:30:00+08:00",
        "last_success_at": None,
        "market_data": {
            "as_of": "2026-10-23T16:00:00-04:00",
            "equities": [{"symbol": "MU", "name": "Micron", "price": 120.50,
                          "change_percent": 2.345, "volume": 1234567,
                          "market_time": "2026-10-23T16:00:00-04:00",
                          "source_url": "https://finance.yahoo.com/quote/MU/"}],
            "commodities": [],
            "gainers": {"rows": [{"symbol": "MU", "price": 120.50, "change_percent": 2.345,
                                  "market_cap": 123456789000, "volume": 1234567,
                                  "turnover": None, "source_url": "https://example.com/MU"}],
                        "limitations": ["榜单只有有限样本。"]},
            "errors": ["布伦特数据缺失。"],
        },
        "news": {"items": [], "errors": ["新闻接口尚未配置。"]},
        "errors": ["布伦特数据缺失。"],
    }


class RendererTests(unittest.TestCase):
    def test_dates_status_numbers_and_missing_values_are_visible(self):
        html = render_report(sample_report(), [])
        self.assertIn("2026-10-24", html)
        self.assertIn("2026-10-23T16:00:00-04:00", html)
        self.assertIn("部分资料缺失", html)
        self.assertIn("$120.50", html)
        self.assertIn("+2.35%", html)
        self.assertIn("1,234,567", html)
        self.assertIn("$123,456,789,000.00", html)
        self.assertIn("未找到可核实催化", html)
        self.assertIn("新闻接口尚未配置。", html)
        self.assertEqual(html.count("布伦特数据缺失。"), 1)
        self.assertNotIn("$0.00", html)

    def test_html_payload_is_escaped_and_unsafe_urls_rejected(self):
        report = sample_report()
        report["report_date"] = '<script>alert("x")</script>'
        report["market_data"]["equities"][0]["source_url"] = "javascript:alert(1)"
        report["news"]["items"] = [{
            "title": '<img src=x onerror="alert(1)">', "summary": "A & B",
            "relevance": "<b>unsafe</b>", "sources": [{"url": "https://user:password@example.com/a", "title": "unsafe"}],
        }]
        html = render_report(report, [{"date": "bad", "url": "//evil.example/a.html"},
                                      {"date": "bad2", "url": "../report.html"}])
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn('href="//evil', html)
        self.assertNotIn('href="../', html)
        self.assertNotIn("user:password", html)
        self.assertIn("&lt;img", html)
        self.assertIn("A &amp; B", html)

    def test_safe_archive_and_source_links_survive(self):
        html = render_report(sample_report(), [{"date": "2026-10-23", "status": "partial",
                                               "url": "archive/2026-10-23/index.html"}])
        self.assertIn('href="archive/2026-10-23/index.html"', html)
        self.assertIn('href="https://finance.yahoo.com/quote/MU/"', html)
        self.assertIn('href="./report.md"', html)
        self.assertIn('href="./report.json"', html)
        sibling_html = render_report(sample_report(), [{"date": "2026-10-23", "status": "partial",
                                                       "url": "../../reports/2026-10-23/index.html"}])
        self.assertIn('href="../../reports/2026-10-23/index.html"', sibling_html)

    def test_encoded_traversal_control_and_non_https_are_rejected(self):
        for url in ["%2e%2e/x.html", "https://example.com/%0afoo", "file:///x.html", "http://example.com/x.html",
                    "https://example.com:bad/x.html", "javascript%3Aalert.html", "https://example.com\\@evil.example"]:
            with self.subTest(url=url):
                html = render_report(sample_report(), [{"date": "unsafe", "url": url}])
                self.assertNotIn(f'href="{url}"', html)

    def test_failed_empty_report_is_explicit(self):
        report = {"status": "failed", "report_date": "2026-10-24", "errors": ["服务不可用"]}
        html = render_report(report, [])
        self.assertIn("本期生成失败", html)
        self.assertIn("本期资料不完整", html)
        self.assertIn("服务不可用", html)
        self.assertIn("没有可验证的涨幅榜", html)
        self.assertNotIn("$0.00", html)

    def test_nan_infinity_and_bool_are_not_displayed_as_quotes(self):
        report = sample_report()
        row = report["market_data"]["equities"][0]
        row.update(price=float("nan"), change_percent=float("inf"), volume=True)
        html = render_report(report, [])
        self.assertNotIn("$nan", html)
        self.assertNotIn("+inf%", html)
        self.assertNotIn("<td>True</td>", html)

    def test_markdown_preserves_status_sources_and_escapes_table_content(self):
        report = sample_report()
        report["market_data"]["equities"][0]["symbol"] = "M|U<script>"
        md = render_markdown(report)
        self.assertIn("部分资料缺失", md)
        self.assertIn("M\\|U&lt;script&gt;", md)
        self.assertIn("https://finance.yahoo.com/quote/MU/", md)
        self.assertIn("未找到可核实催化", md)
        self.assertIn("$120.50", md)
        self.assertIn("新闻接口尚未配置", md)

    def test_news_warnings_are_visible_without_becoming_errors(self):
        report = sample_report()
        report["status"] = "complete"
        report["errors"] = []
        report["market_data"]["errors"] = []
        report["news"] = {"items": [], "errors": [], "warnings": ["工具引用来源不是独立核实全文。"]}
        html = render_report(report, [])
        self.assertIn("工具引用来源不是独立核实全文。", html)
        self.assertNotIn("本期资料不完整", html)
        self.assertIn("工具引用来源不是独立核实全文。", render_markdown(report))

    def test_market_agent_turnover_aliases_remain_labeled_estimates(self):
        report = sample_report()
        row = report["market_data"]["gainers"]["rows"][0]
        del row["turnover"]
        row["estimated_turnover_usd"] = 1234567
        row["turnover_basis"] = "报价乘成交量的估算，非实际成交额"
        html = render_report(report, [])
        self.assertIn("$1,234,567.00", html)
        self.assertIn("报价乘成交量的估算，非实际成交额", html)
        md = render_markdown(report)
        self.assertIn("$1,234,567.00", md)
        self.assertIn("报价乘成交量的估算，非实际成交额", md)

    def test_failed_retry_preserves_complete_status_and_discloses_attempt(self):
        report = sample_report()
        report["status"] = "complete"
        report["errors"] = []
        report["market_data"]["errors"] = []
        report["news"]["errors"] = []
        report["latest_attempt"] = {"generated_at": "2026-10-24T09:00:00+08:00",
                                    "status": "failed", "errors": ["重试数据接口不可用"]}
        report["market_data"]["gainers"]["rows"][0]["related_sources"] = [
            {"url": "https://example.com/micron-news", "title": "美光相关新闻"}]
        html = render_report(report, [])
        self.assertIn("资料齐备", html)
        self.assertNotIn("本期资料不完整", html)
        self.assertIn("最近一次重试未成功，以下保留先前版本", html)
        self.assertIn("2026-10-24T09:00:00+08:00", html)
        self.assertIn("重试数据接口不可用", html)
        self.assertIn('href="https://example.com/micron-news"', html)
        md = render_markdown(report)
        self.assertIn("状态：资料齐备", md)
        self.assertIn("最近一次重试未成功，以下保留先前版本", md)
        self.assertIn("重试数据接口不可用", md)
        self.assertIn("https://example.com/micron-news", md)


if __name__ == "__main__":
    unittest.main()
