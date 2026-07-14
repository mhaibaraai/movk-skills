#!/usr/bin/env python3
"""engines.py 纯函数单元测试，不发起任何网络请求。

  uv run --with pytest pytest skills/web-fetch/tests/ -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import engines  # noqa: E402


class TestEngineChain:
    def test_order_puts_reader_proxy_before_playwright(self):
        assert engines.ENGINE_NAMES == ("urllib", "curl_cffi", "reader_proxy", "playwright")

    def test_every_engine_has_impl(self):
        assert set(engines._ENGINE_FUNCS) == set(engines.ENGINE_NAMES)


class TestChallengeDetection:
    def test_riverside_js_challenge(self):
        """瑞数挑战页：即便以 200 返回也必须判定为未过关。"""
        body = b"<html><head><script>$_ts=window['$_ts'];if(!$_ts)$_ts={};</script></head></html>" + b"x" * 600
        assert engines._looks_blocked(200, body, "text/html")

    def test_jsluid_cookie_challenge(self):
        body = b"<script>document.cookie='__jsluid_s=abc';location.reload();</script>" + b"x" * 600
        assert engines._looks_blocked(200, body, "text/html")

    def test_http_412(self):
        assert engines._looks_blocked(412, b"<html>whatever</html>", "text/html")

    def test_normal_page_passes(self):
        body = ("<html><body>" + "正文内容 " * 200 + "</body></html>").encode()
        assert not engines._looks_blocked(200, body, "text/html; charset=utf-8")


class TestReaderProxy:
    def test_url_prefixes_endpoint(self):
        assert engines._reader_proxy_url("https://www.cnpc.com.cn/a.shtml") == (
            "https://r.jina.ai/https://www.cnpc.com.cn/a.shtml"
        )

    def test_endpoint_override(self, monkeypatch):
        monkeypatch.setenv("WEB_FETCH_READER_ENDPOINT", "https://reader.example.com/")
        assert engines._reader_proxy_url("https://a.com/") == "https://reader.example.com/https://a.com/"

    def test_endpoint_override_without_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("WEB_FETCH_READER_ENDPOINT", "https://reader.example.com")
        assert engines._reader_proxy_url("https://a.com/") == "https://reader.example.com/https://a.com/"

    def test_requests_rendered_html(self, monkeypatch):
        monkeypatch.delenv("JINA_API_KEY", raising=False)
        headers = engines._reader_proxy_headers()
        assert headers["X-Return-Format"] == "html"
        assert "Authorization" not in headers

    def test_api_key_becomes_bearer_token(self, monkeypatch):
        monkeypatch.setenv("JINA_API_KEY", "secret-token")
        assert engines._reader_proxy_headers()["Authorization"] == "Bearer secret-token"

    @pytest.mark.parametrize("url", [
        "http://localhost:8080/admin",
        "http://127.0.0.1/",
        "https://10.1.2.3/internal",
        "https://192.168.1.1/router",
        "https://172.16.0.9/x",
        "https://169.254.169.254/latest/meta-data/",
        "https://nas.local/files",
    ])
    def test_private_targets_never_leave_the_machine(self, url):
        """内网地址绝不能外发给第三方代理。"""
        assert engines._is_private_target(url)
        with pytest.raises(engines.ProxyRefused):
            engines._fetch_reader_proxy(url, timeout=1)

    @pytest.mark.parametrize("url", ["https://www.cnpc.com.cn/a.shtml", "https://iea.org/reports"])
    def test_public_targets_allowed(self, url):
        assert not engines._is_private_target(url)


class FakePage:
    """按剧本逐次返回页面内容；"reloading" 表示此刻页面正在重载，取内容会抛异常。"""

    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls = 0

    def _current(self) -> str:
        self.calls += 1
        state = self.script.pop(0) if self.script else "<html>正文</html>"
        if state == "reloading":
            raise RuntimeError("Execution context was destroyed, most likely because of a navigation")
        return state

    def title(self) -> str:
        return ""

    def content(self) -> str:
        return self._current()

    def locator(self, _selector):
        raise RuntimeError("no such element")


class TestWaitForChallenge:
    @pytest.fixture(autouse=True)
    def no_sleep(self, monkeypatch):
        monkeypatch.setattr(engines.time, "sleep", lambda _s: None)

    def test_reload_exception_is_not_mistaken_for_success(self):
        """页面重载中取不到内容 != 挑战已通过 —— 误判会抓走挑战页而非真正文。"""
        page = FakePage(["reloading"] * 5)
        assert engines._wait_for_challenge(page, max_attempts=5) is False

    def test_waits_through_reload_until_real_content(self):
        page = FakePage(["<script>$_ts=window['$_ts']</script>", "reloading", "<html>真正文</html>"])
        assert engines._wait_for_challenge(page, max_attempts=5) is True

    def test_clean_page_passes_immediately(self):
        page = FakePage(["<html>真正文</html>"])
        assert engines._wait_for_challenge(page, max_attempts=5) is True
        assert page.calls == 1


class TestProbe:
    def test_reader_proxy_reported(self):
        engines.probe_engines.cache_clear()
        assert engines.probe_engines()["reader_proxy"] is True

    def test_reader_proxy_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("WEB_FETCH_NO_READER_PROXY", "1")
        engines.probe_engines.cache_clear()
        assert engines.probe_engines()["reader_proxy"] is False
        engines.probe_engines.cache_clear()
