#!/usr/bin/env python3
"""sitemap.py 纯函数单元测试，不发起任何网络请求。

  uv run --with pytest pytest skills/web-fetch/tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sitemap  # noqa: E402

URLSET = '<?xml version="1.0"?><urlset><url><loc>https://x/a.html</loc></url></urlset>'
NOT_FOUND = {"kind": "http_error", "detail": "HTTP 404"}
BLOCKED = {"kind": "blocked", "detail": "命中反爬/JS 挑战页特征"}


class TestRoots:
    """裸域名未必对外服务：cnpc.com.cn 实测 DNS 解析失败，www.cnpc.com.cn 才是本体。

    曾经只对 host.count(".") == 1 补 www，.com.cn/.gov.cn 这类两段式后缀（中国政务与央企
    站点极常见）因此从未被试过 www 变体，把「问都没问对主机」误报成「站点没有 sitemap」。
    """

    def test_single_label_suffix_gets_www(self):
        assert sitemap._roots("iea.org") == ["https://iea.org", "https://www.iea.org"]

    def test_two_label_suffix_also_gets_www(self):
        assert sitemap._roots("cnpc.com.cn") == [
            "https://cnpc.com.cn",
            "https://www.cnpc.com.cn",
        ]

    def test_existing_www_not_doubled(self):
        assert sitemap._roots("www.ndrc.gov.cn") == ["https://www.ndrc.gov.cn"]

    def test_subdomain_keeps_bare_host_first(self):
        """子域名站点会多探一个通常不存在的 www.news.… —— 探测失败代价远低于漏判。"""
        roots = sitemap._roots("news.cnpc.com.cn")
        assert roots[0] == "https://news.cnpc.com.cn"

    def test_scheme_preserved(self):
        assert sitemap._roots("http://example.com")[0] == "http://example.com"


class TestDiscover:
    def _stub(self, monkeypatch, responses):
        """responses: {url: (text, error)}，未列出的 URL 一律当 404。"""
        def fake(url, engine):
            return responses.get(url, ("", NOT_FOUND))
        monkeypatch.setattr(sitemap, "_fetch_xml", fake)

    def test_robots_declaration_wins(self, monkeypatch):
        self._stub(monkeypatch, {
            "https://iea.org/robots.txt": ("Sitemap: https://iea.org/sitemap-1.xml", None),
        })
        roots, error = sitemap.discover("iea.org", "http")
        assert roots == ["https://iea.org/sitemap-1.xml"]
        assert error is None

    def test_falls_back_to_sitemap_xml(self, monkeypatch):
        self._stub(monkeypatch, {
            "https://iea.org/robots.txt": ("User-agent: *", None),
            "https://iea.org/sitemap.xml": (URLSET, None),
        })
        roots, error = sitemap.discover("iea.org", "http")
        assert roots == ["https://iea.org/sitemap.xml"]
        assert error is None

    def test_www_variant_reached_for_two_label_suffix(self, monkeypatch):
        """裸域全挂、www 变体有 sitemap —— 修复前这条路径根本走不到。"""
        self._stub(monkeypatch, {
            "https://www.cnpc.com.cn/robots.txt": ("User-agent: *", None),
            "https://www.cnpc.com.cn/sitemap.xml": (URLSET, None),
        })
        roots, error = sitemap.discover("cnpc.com.cn", "http")
        assert roots == ["https://www.cnpc.com.cn/sitemap.xml"]
        assert error is None

    def test_nothing_anywhere_is_no_sitemap(self, monkeypatch):
        self._stub(monkeypatch, {})
        roots, error = sitemap.discover("example.com", "http")
        assert roots == []
        assert error["kind"] == "no_sitemap"

    def test_blocked_robots_not_reported_as_no_sitemap(self, monkeypatch):
        """robots.txt 被反爬挡住而 /sitemap.xml 只是 404 时，结论是「被拦截」而非「没有」——
        前者该换引擎重试或回落 search，后者是确定性结论，混为一谈会误导调用方。"""
        self._stub(monkeypatch, {
            "https://example.com/robots.txt": ("", BLOCKED),
        })
        roots, error = sitemap.discover("example.com", "http")
        assert roots == []
        assert error["kind"] == "blocked"

    def test_blocked_sitemap_xml_also_propagated(self, monkeypatch):
        self._stub(monkeypatch, {
            "https://example.com/robots.txt": ("User-agent: *", None),
            "https://example.com/sitemap.xml": ("", BLOCKED),
        })
        _, error = sitemap.discover("example.com", "http")
        assert error["kind"] == "blocked"

    def test_valid_sitemap_wins_over_earlier_block(self, monkeypatch):
        """裸域被拦、www 变体拿到了有效 sitemap —— 拿到了就是拿到了，不该报 blocked。"""
        self._stub(monkeypatch, {
            "https://example.com/robots.txt": ("", BLOCKED),
            "https://www.example.com/robots.txt": ("User-agent: *", None),
            "https://www.example.com/sitemap.xml": (URLSET, None),
        })
        roots, error = sitemap.discover("example.com", "http")
        assert roots == ["https://www.example.com/sitemap.xml"]
        assert error is None

    def test_html_error_page_is_not_a_sitemap(self, monkeypatch):
        """软 404：HTTP 200 返回一页 HTML。_XML_RE 哨兵挡下，不能当成空 sitemap。"""
        self._stub(monkeypatch, {
            "https://example.com/robots.txt": ("User-agent: *", None),
            "https://example.com/sitemap.xml": ("<html><body>页面不存在</body></html>", None),
        })
        roots, error = sitemap.discover("example.com", "http")
        assert roots == []
        assert error["kind"] == "no_sitemap"
