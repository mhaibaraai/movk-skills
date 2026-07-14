#!/usr/bin/env python3
"""engines.py 纯函数单元测试，不发起任何网络请求。

  uv run --with pytest pytest skills/web-fetch/tests/ -q
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import engines  # noqa: E402


class TestEngineChain:
    def test_two_engines_http_then_browser(self):
        assert engines.ENGINE_NAMES == ("http", "browser")

    def test_browser_timeout_floor_covers_slow_domestic_sites(self):
        """实测 so.com 首跳 19.4s、news.cnpc.com.cn 31.6s，20s 的默认超时必然误杀。"""
        assert engines.BROWSER_MIN_TIMEOUT >= 45


class TestBlockedReason:
    def test_riverside_js_challenge(self):
        """瑞数挑战页：即便以 200 返回也必须判定为未过关。"""
        body = b"<html><head><script>$_ts=window['$_ts'];if(!$_ts)$_ts={};</script></head></html>" + b"x" * 600
        assert engines._blocked_reason(200, body, "text/html")[0] == "challenge"

    def test_jsluid_cookie_challenge(self):
        body = b"<script>document.cookie='__jsluid_s=abc';location.reload();</script>" + b"x" * 600
        assert engines._blocked_reason(200, body, "text/html")[0] == "challenge"

    def test_http_412_is_reported_with_status(self):
        kind, detail = engines._blocked_reason(412, b"<html>whatever</html>", "text/html")
        assert kind == "http_error"
        assert "412" in detail

    def test_js_shell_too_small(self):
        kind, _ = engines._blocked_reason(200, b"<html></html>", "text/html")
        assert kind == "empty_body"

    def test_normal_page_passes(self):
        body = ("<html><body>" + "正文内容 " * 200 + "</body></html>").encode()
        assert engines._blocked_reason(200, body, "text/html; charset=utf-8") is None

    def test_pdf_bypasses_html_heuristics(self):
        assert engines._blocked_reason(200, b"%PDF-1.4 tiny", "application/pdf") is None

    def test_360_ip_block_page(self):
        """360 的 IP 层拦截页：HTTP 200、体积正常、不含 JS 挑战特征，仅标题可辨。"""
        body = b"<html><head><title>\xe8\xae\xbf\xe9\x97\xae\xe5\xbc\x82\xe5\xb8\xb8\xe9\xa1\xb5\xe9\x9d\xa2</title></head><body>" + b"x" * 9000
        assert engines._blocked_reason(200, body, "text/html; charset=utf-8")[0] == "challenge"


class TestExpectSentinel:
    """expect 哨兵：黑名单认不出的拦截页，靠调用方声明的「有效响应结构」来识别。"""

    SERP = re.compile(r"_360搜索\s*</title>", re.I)

    def _page(self, title: str) -> bytes:
        return f"<html><head><title>{title}</title></head><body>".encode() + b"x" * 9000

    def test_valid_serp_passes(self):
        assert engines._blocked_reason(200, self._page("shell report_360搜索"), "text/html", self.SERP) is None

    def test_zero_result_serp_still_passes(self):
        """零结果 SERP 仍是有效响应，不能误判为拦截——否则会白白升级到浏览器。"""
        page = self._page("zzqqxx9988 fakenonsense_360搜索")
        assert engines._blocked_reason(200, page, "text/html", self.SERP) is None

    def test_structurally_wrong_page_rejected(self):
        kind, _ = engines._blocked_reason(200, self._page("某站首页"), "text/html", self.SERP)
        assert kind == "unexpected_structure"

    def test_no_expect_keeps_old_behaviour(self):
        assert engines._blocked_reason(200, self._page("某站首页"), "text/html") is None


class TestEmptyShellNeverReportedAsSuccess:
    """核心回归：抓到空壳必须报错，绝不能返回一条 length=20 的「成功」。

    中国石油官网的瑞数挑战未通过时，页面被清空、只剩一个 <title>。旧实现会把它当成功
    返回，下游技能就拿着 20 个字符去写「深度分析」。
    """

    SHELL = (
        "<html><head><title>中国石油2025年度企业社会责任报告发布</title></head>"
        "<body></body></html>"
    ).encode()

    def _fetched(self, data: bytes) -> dict:
        return {"url": "https://www.cnpc.com.cn/x.shtml", "data": data,
                "content_type": "text/html; charset=utf-8", "status": 200,
                "engine_used": "browser", "attempts": []}

    def test_title_only_page_is_an_error(self):
        result = engines._clean_one(self._fetched(self.SHELL), max_chars=8000, max_pages=30)
        assert "error" in result
        assert "text" not in result

    def test_substantive_page_succeeds(self):
        body = ("<html><head><title>真文章</title><body>" + "正文内容。" * 100 + "</body></html>").encode()
        result = engines._clean_one(self._fetched(body), max_chars=8000, max_pages=30)
        assert result.get("length", 0) >= engines.MIN_TEXT_CHARS
        assert "error" not in result

    def test_min_text_chars_is_meaningfully_above_a_bare_title(self):
        assert engines.MIN_TEXT_CHARS > 100


class TestFailureAttempts:
    """失败必须逐层说清原因，而不是压成一句「所有引擎均失败」。"""

    def test_attempts_carried_into_cleaned_failure(self):
        fetched = {
            "url": "https://x.com/",
            "data": None,
            "error": "各层引擎均未拿到有效内容（http HTTP 412；browser 命中反爬/JS 挑战页特征）",
            "attempts": [
                {"engine": "http", "kind": "http_error", "detail": "HTTP 412"},
                {"engine": "browser", "kind": "challenge", "detail": "命中反爬/JS 挑战页特征"},
            ],
            "engines_available": {"http": True, "browser": True, "chromium": True},
        }
        result = engines._clean_one(fetched, max_chars=8000, max_pages=30)
        assert [a["engine"] for a in result["attempts"]] == ["http", "browser"]
        assert result["attempts"][0]["kind"] == "http_error"

    def test_timeout_classified_apart_from_network(self):
        class FakeTimeout(Exception):
            pass
        FakeTimeout.__name__ = "TimeoutError"
        assert engines._exception_reason(FakeTimeout("Page.goto timed out"))[0] == "timeout"
        assert engines._exception_reason(ValueError("boom"))[0] == "network"

    def test_content_too_large_has_its_own_kind(self):
        assert engines._exception_reason(engines.ContentTooLarge(50 * 1024 * 1024))[0] == "too_large"


class TestDownloadDetection:
    """浏览器 goto 一个 PDF 会抛 Download is starting —— 必须识别并改走请求上下文取字节。"""

    @pytest.mark.parametrize("message", [
        "Page.goto: Download is starting",
        "Page.goto: net::ERR_ABORTED at https://x.com/a.pdf",
    ])
    def test_download_errors_recognised(self, message):
        assert engines._DOWNLOAD_RE.search(message)

    def test_ordinary_timeout_not_mistaken_for_download(self):
        assert not engines._DOWNLOAD_RE.search("Page.goto: Timeout 45000ms exceeded")


class TestProbe:
    def test_probe_reports_both_engines(self):
        engines.probe_engines.cache_clear()
        avail = engines.probe_engines()
        assert set(avail) == {"http", "browser", "chromium"}
        engines.probe_engines.cache_clear()
