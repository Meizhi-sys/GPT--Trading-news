import io
import json
import os
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

from scripts.news_analysis import _parse, _payload, _source_url, collect_news


NOW = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
URL = "https://investors.example.com/releases/test"


def news_item(**overrides):
    item = {
        "title": "公司发布业绩指引",
        "category": "半导体存储",
        "event_date": "2026-10-24",
        "published_at": "2026-10-24T18:00:00-04:00",
        "summary": "公司发布新的业绩指引。",
        "relevance": "应结合原文观察存储需求变化。",
        "related_symbols": ["MU"],
        "sources": [{"url": URL, "title": "业绩公告"}],
    }
    item.update(overrides)
    return item


def response(items=None, sources=None, annotations=None):
    return {
        "status": "completed",
        "output": [
            {
                "type": "web_search_call",
                "action": {"type": "search", "sources": sources if sources is not None else [{"url": URL, "title": "公司公告"}]},
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({"items": items if items is not None else [news_item()]}),
                        "annotations": annotations or [],
                    }
                ],
            },
        ],
    }


class NewsAnalysisTests(unittest.TestCase):
    def test_no_key_never_calls_network(self):
        with patch.dict(os.environ, {}, clear=True), patch("urllib.request.urlopen") as network:
            result = collect_news(NOW, {}, "2026-10-23")
        network.assert_not_called()
        self.assertEqual(result["items"], [])
        self.assertIn("OPENAI_API_KEY", result["errors"][0])

    def test_accepts_only_source_returned_by_search(self):
        result = _parse(response(), NOW)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["sources"][0]["title"], "公司公告")
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["warnings"])

    def test_rejects_hallucinated_source(self):
        result = _parse(response([news_item(sources=[{"url": "https://fake.example/news", "title": "Fake"}])]), NOW)
        self.assertEqual(result["items"], [])
        self.assertTrue(any("未被搜索工具引用" in err for err in result["errors"]))

    def test_citation_annotation_is_allowed(self):
        result = _parse(response(sources=[], annotations=[{"type": "url_citation", "url": URL, "title": "引用公告"}]), NOW)
        self.assertEqual(len(result["items"]), 1)

    def test_source_fragment_normalization(self):
        result = _parse(response([news_item(sources=[{"url": URL + "#section", "title": "x"}])]), NOW)
        self.assertEqual(result["items"][0]["sources"][0]["url"], URL)

    def test_rejects_old_future_and_naive_publication_times(self):
        for timestamp in ["2026-10-22T18:00:00Z", "2026-10-26T00:00:00Z", "2026-10-24T18:00:00"]:
            with self.subTest(timestamp=timestamp):
                result = _parse(response([news_item(published_at=timestamp)]), NOW)
                self.assertEqual(result["items"], [])
                self.assertTrue(any("日期" in err for err in result["errors"]))

    def test_rejects_incomplete_or_malformed_response(self):
        bad = response()
        bad["status"] = "incomplete"
        self.assertEqual(_parse(bad, NOW)["items"], [])
        bad = response()
        bad["output"][1]["content"][0]["text"] = "not json"
        self.assertIn("JSON", _parse(bad, NOW)["errors"][0])

    def test_rejects_insecure_or_credential_urls(self):
        for url in ["http://example.com/news", "https://localhost/a", "https://127.0.0.1/a", "https://u:secret@example.com/a", "https://example.com:8443/a"]:
            with self.subTest(url=url):
                self.assertIsNone(_source_url(url))

    def test_duplicate_title_not_published_twice(self):
        result = _parse(response([news_item(), news_item()]), NOW)
        self.assertEqual(len(result["items"]), 1)

    def test_empty_news_explicitly_does_not_claim_no_events(self):
        result = _parse(response([]), NOW)
        self.assertEqual(result["items"], [])
        self.assertTrue(any("不代表当天没有" in err for err in result["errors"]))

    def test_payload_requires_search_and_structured_output(self):
        payload = _payload(NOW, {}, "2026-10-23", [])
        self.assertEqual(payload["tools"], [{"type": "web_search"}])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertFalse(payload["store"])

    def test_candidate_input_includes_symbols_and_names_without_prices(self):
        payload = _payload(NOW, {
            "equities": [{"symbol": "MU", "price": 999.0}],
            "gainers": {"rows": [{"symbol": "TEST", "name": "Example Co", "price": 123.45}]},
        }, "2026-10-23", [])
        context = json.loads(payload["input"][1]["content"])
        self.assertEqual(context["gainer_candidates_to_research"], [{"symbol": "TEST", "name": "Example Co"}])
        self.assertEqual(context["watchlist"], ["MU", "MRNA", "TEST"])
        self.assertNotIn("999.0", payload["input"][1]["content"])
        self.assertNotIn("123.45", payload["input"][1]["content"])

    def test_related_symbols_are_valid_uppercase_unique_and_bounded(self):
        symbols = ["MU", "MU", "mrna", "<script>", " AAPL ", "BRK.B", "ABC-DEF"] + [f"T{i}" for i in range(20)]
        result = _parse(response([news_item(related_symbols=symbols)]), NOW)
        actual = result["items"][0]["related_symbols"]
        self.assertEqual(actual[:4], ["MU", "AAPL", "BRK.B", "ABC-DEF"])
        self.assertNotIn("mrna", actual)
        self.assertNotIn("<script>", actual)
        self.assertEqual(len(actual), 10)

    def test_related_symbols_required_but_empty_list_is_valid(self):
        item = news_item()
        del item["related_symbols"]
        self.assertEqual(_parse(response([item]), NOW)["items"], [])
        self.assertEqual(len(_parse(response([news_item(related_symbols=[])]), NOW)["items"]), 1)

    def test_api_success_parses_and_keeps_secrets_out_of_body(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}, clear=True), patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response()).encode())) as network:
            result = collect_news(NOW, {}, "2026-10-23")
        self.assertEqual(len(result["items"]), 1)
        request = network.call_args.args[0]
        self.assertNotIn(b"test-secret", request.data)
        self.assertEqual(network.call_args.kwargs["timeout"], 90)

    def test_http_auth_failure_not_retried_or_leaked(self):
        error = urllib.error.HTTPError("https://api.openai.com/v1/responses", 401, "secret-string", {}, None)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}, clear=True), patch("urllib.request.urlopen", side_effect=error) as network:
            result = collect_news(NOW, {}, "2026-10-23")
        self.assertEqual(network.call_count, 1)
        self.assertIn("401", result["errors"][0])
        self.assertNotIn("secret", json.dumps(result))

    def test_transient_errors_have_bounded_retry(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}, clear=True), patch("urllib.request.urlopen", side_effect=urllib.error.URLError("secret-proxy-error")) as network, patch("time.sleep"):
            result = collect_news(NOW, {}, "2026-10-23")
        self.assertEqual(network.call_count, 2)
        self.assertEqual(result["items"], [])
        self.assertNotIn("secret", json.dumps(result))

    def test_naive_now_rejected(self):
        with self.assertRaises(ValueError):
            collect_news(datetime(2026, 10, 25), {}, "2026-10-23")


if __name__ == "__main__":
    unittest.main()
