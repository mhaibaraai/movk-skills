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


class TestBrowserStatusNotWhitewashed:
    """核心回归：渲染出正文只能洗白「挑战类」状态码，不能洗白 404。

    gov.cn 对任何不存在的路径返回 404 + 一个 JS 跳首页的错误页。浏览器导航后跳到首页、
    渲染出 2781 字符正文，旧实现据此把 404 改写成 200，于是中国政府网首页被当作
    「政策 PDF 正文」返回给下游解读。
    """

    def test_404_stays_404_even_after_body_renders(self):
        assert engines._resolve_browser_status(404, passed=True) == 404

    def test_410_stays_410(self):
        assert engines._resolve_browser_status(410, passed=True) == 410

    def test_riverside_412_whitewashed_once_body_renders(self):
        """瑞数首跳 412 + 混淆脚本，脚本跑完才有正文——这才是洗白的正当场景。"""
        assert engines._resolve_browser_status(412, passed=True) == 200

    @pytest.mark.parametrize("status", sorted(engines.CHALLENGE_STATUS))
    def test_challenge_statuses_whitewashed(self, status):
        assert engines._resolve_browser_status(status, passed=True) == 200

    def test_nothing_whitewashed_when_body_never_rendered(self):
        assert engines._resolve_browser_status(412, passed=False) == 412

    def test_404_from_browser_reported_as_http_error(self):
        """洗白被拦下之后，_blocked_reason 必须把它判成 http_error 而非放行。"""
        gov_404_page = ("<html><body>" + "跳转首页 " * 300 + "</body></html>").encode()
        status = engines._resolve_browser_status(404, passed=True)
        kind, detail = engines._blocked_reason(status, gov_404_page, "text/html")
        assert kind == "http_error"
        assert "404" in detail


class TestPdfDetection:
    """PDF 按魔数认，而不是只信 Content-Type——政府站常把 PDF 标成 octet-stream。"""

    PDF_BYTES = b"%PDF-1.7\n" + b"x" * 600

    def test_magic_number_beats_octet_stream(self):
        assert engines._is_pdf(self.PDF_BYTES, "application/octet-stream")

    def test_content_type_alone_is_enough(self):
        assert engines._is_pdf(b"whatever", "application/pdf")

    def test_html_is_not_pdf(self):
        assert not engines._is_pdf(b"<html><body>hi</body></html>", "text/html")

    def test_octet_stream_pdf_routed_to_pdf_extractor(self, monkeypatch):
        """否则 PDF 二进制会被 decode 成乱码，当作一篇 html 正文返回。

        桩掉 _extract_pdf：这里验证的是路由选择，不是 pypdf 的抽取能力（pypdf 是脚本的
        PEP 723 依赖，单元测试环境里没有它，也不该为一个路由断言把它拖进来）。
        """
        monkeypatch.setattr(engines, "_extract_pdf",
                            lambda data, max_chars, max_pages: {"type": "pdf", "text": "stub"})
        fetched = {"url": "https://x.gov.cn/a.pdf", "data": self.PDF_BYTES,
                   "content_type": "application/octet-stream", "status": 200,
                   "engine_used": "http", "attempts": []}
        result = engines._clean_one(fetched, max_chars=8000, max_pages=30)
        assert result["type"] == "pdf"

    def test_pdf_url_returning_html_is_rejected(self):
        """请求 .pdf 却拿到 HTML：链接失效或被重定向到错误页，绝不能当正文返回。"""
        page = ("<html><body>" + "首页内容 " * 300 + "</body></html>").encode()
        kind, _ = engines._blocked_reason(200, page, "text/html",
                                          url="https://www.gov.cn/x/files/7072084.pdf")
        assert kind == "wrong_content_type"

    def test_pdf_url_returning_real_pdf_passes(self):
        assert engines._blocked_reason(200, self.PDF_BYTES, "application/octet-stream",
                                       url="https://x.gov.cn/a.pdf") is None

    def test_html_url_returning_html_unaffected(self):
        page = ("<html><body>" + "正文 " * 300 + "</body></html>").encode()
        assert engines._blocked_reason(200, page, "text/html",
                                       url="https://x.gov.cn/a.html") is None


class TestAttachments:
    """政策条款几乎总在附件里，正文通知页往往只有一句「现将《XX》印发给你们」。

    附件链接就明明白白挂在 HTML 里，清洗时被抹掉了，下游只好按 URL 规律硬编——猜错就
    撞上站点错误页。把链接提出来还给调用方，猜测就没有存在的理由。
    """

    BASE = "https://www.ndrc.gov.cn/xxgk/zcfb/tz/202606/t20260615_1405852.html"
    PAGE = (
        '<html><body><p>现将《重点行业节能降碳改造攻坚三年行动计划》印发给你们。</p>'
        '<a href="./P020260615375309063825.pdf">附件：三年行动计划</a>'
        '<a href="./P020260615375739523059.ofd">附件：三年行动计划</a>'
        '<a href="../../../jd/jd/202606/t20260615_1405864.html">政策解读</a>'
        "</body></html>"
    )

    def test_relative_links_resolved_to_absolute(self):
        found = engines.extract_attachments(self.PAGE, self.BASE)
        assert [a["url"] for a in found] == [
            "https://www.ndrc.gov.cn/xxgk/zcfb/tz/202606/P020260615375309063825.pdf",
            "https://www.ndrc.gov.cn/xxgk/zcfb/tz/202606/P020260615375739523059.ofd",
        ]

    def test_extension_recorded_and_order_preserved(self):
        assert [a["ext"] for a in engines.extract_attachments(self.PAGE, self.BASE)] == ["pdf", "ofd"]

    def test_ordinary_page_links_ignored(self):
        urls = [a["url"] for a in engines.extract_attachments(self.PAGE, self.BASE)]
        assert not any(u.endswith(".html") for u in urls)

    def test_duplicates_collapsed(self):
        page = '<a href="/a.pdf">x</a><a href="/a.pdf">同一份，两个入口</a>'
        assert len(engines.extract_attachments(page, self.BASE)) == 1

    def test_attachments_surfaced_in_cleaned_result(self):
        fetched = {"url": self.BASE, "data": self.PAGE.encode() + "正文 ".encode() * 200,
                   "content_type": "text/html; charset=utf-8", "status": 200,
                   "engine_used": "http", "attempts": []}
        result = engines._clean_one(fetched, max_chars=8000, max_pages=30)
        assert [a["ext"] for a in result["attachments"]] == ["pdf", "ofd"]

    def test_key_absent_when_page_has_no_attachments(self):
        """空数组会诱使调用方以为「找过了、确实没有」，缺席才是诚实的表达。"""
        fetched = {"url": self.BASE, "data": ("<html><body>" + "正文 " * 300 + "</body></html>").encode(),
                   "content_type": "text/html; charset=utf-8", "status": 200,
                   "engine_used": "http", "attempts": []}
        assert "attachments" not in engines._clean_one(fetched, max_chars=8000, max_pages=30)


class TestExtractLinksAnchorText:
    """锚文本是同页多份 PDF 之间唯一的判别依据，串位比缺失更危险。

    CNPC 首页实测：附件 5 条，URL 全是拼音缩写路径（qyshzrbg/ndbg/hsebg），单看 URL 分不出
    哪条是目标；而页面里的 <a> 大量未闭合，用正则跨标签抓锚文本会越过锚点边界，把「集团公司
    2025年社会责任报告」绑到相邻那份 2021 年合规手册上——调用方会拿着一个自信的标签去抓错文件。
    下面的样本就是这个结构。
    """

    BASE = "https://www.cnpc.com.cn/"
    PAGE = (
        '<html><body>'
        '<a href="/dxcy/2021hgsc/index.shtml">2021年合规手册'  # 未闭合，浏览器遇新 <a> 自动断开
        '<a href="/cnpc/qyshzrbg/202506/P020250612.pdf">集团公司2025年社会责任报告</a>'
        '<a href="/cnpc/ndbg/202504/P020250408.pdf"><img src="/img/cover.png" alt="年度报告"></a>'
        '<a href="/cnpc/hsebg/202503/P020250320.pdf"><img src="/img/hse.png"></a>'
        '<a href="https://www.gov.cn/">中国政府网</a>'
        '<a href="https://www.sasac.gov.cn/report.pdf">国资委引用报告</a>'
        '<a href="https://news.cnpc.com.cn/system/2025/list.shtml">集团要闻</a>'
        '<a href="/nav/icon.shtml"></a>'
        '<a href="javascript:void(0)">展开</a>'
        '</body></html>'
    )

    def _links(self):
        return engines.extract_links(self.PAGE, self.BASE)

    def test_anchor_text_not_bound_to_neighbouring_link(self):
        """未闭合的 <a> 不得让锚文本跨越锚点边界——这正是正则实现踩过的坑。"""
        attachments, pages = self._links()
        report = next(a for a in attachments if a["url"].endswith("P020250612.pdf"))
        assert report["text"] == "集团公司2025年社会责任报告"
        manual = next(p for p in pages if p["url"].endswith("/dxcy/2021hgsc/index.shtml"))
        assert manual["text"] == "2021年合规手册"

    def test_image_link_falls_back_to_alt(self):
        attachments, _ = self._links()
        annual = next(a for a in attachments if a["url"].endswith("P020250408.pdf"))
        assert annual["text"] == "年度报告"

    def test_image_link_without_alt_stays_empty_rather_than_guessed(self):
        """猜不出来就留空。编一个锚文本比没有锚文本更糟：调用方会信它。"""
        attachments, _ = self._links()
        hse = next(a for a in attachments if a["url"].endswith("P020250320.pdf"))
        assert hse["text"] == ""

    def test_offsite_attachment_kept(self):
        """其他域名的附件仍可能是合规引用，附件不受同域限制。"""
        attachments, _ = self._links()
        assert any(a["url"] == "https://www.sasac.gov.cn/report.pdf" for a in attachments)

    def test_offsite_page_link_excluded(self):
        _, pages = self._links()
        assert not any("gov.cn" in p["url"] for p in pages)

    def test_subdomain_page_link_kept(self):
        _, pages = self._links()
        assert any(p["url"].startswith("https://news.cnpc.com.cn/") for p in pages)

    def test_page_link_without_anchor_text_dropped(self):
        """导航图标一类无锚文本的链接对调用方没有判别价值。"""
        _, pages = self._links()
        assert all(p["text"] for p in pages)
        assert not any("/nav/icon.shtml" in p["url"] for p in pages)

    def test_javascript_scheme_skipped(self):
        _, pages = self._links()
        assert not any(p["url"].startswith("javascript:") for p in pages)

    def test_extract_attachments_stays_backward_compatible(self):
        attachments, _ = self._links()
        assert engines.extract_attachments(self.PAGE, self.BASE) == attachments
        assert set(attachments[0]) == {"url", "ext", "text"}

    def test_links_surfaced_only_when_requested(self):
        fetched = {"url": self.BASE, "data": self.PAGE.encode() + "正文 ".encode() * 200,
                   "content_type": "text/html; charset=utf-8", "status": 200,
                   "engine_used": "browser", "attempts": []}
        assert "links" not in engines._clean_one(fetched, max_chars=8000, max_pages=30)
        result = engines._clean_one(fetched, max_chars=8000, max_pages=30, include_links=True)
        assert any(p["text"] == "集团要闻" for p in result["links"])


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
