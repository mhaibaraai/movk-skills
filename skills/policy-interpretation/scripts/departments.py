#!/usr/bin/env python3
"""
部委官网抓取配置。纯数据，不含请求逻辑。

DEPARTMENTS 为已核实可直连抓取的部委，listing_url 均为服务端渲染的列表页，
link_pattern 从原始 HTML 提取 (href, date_hint, title) 三组；date_hint 为
8 位 YYYYMMDD 或 4 位 YYYY。

ASSOCIATED_DOMAINS 为不维护抓取规则的关联部委，仅提供域名供网页检索做 site: 限定。
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DepartmentSource:
    name: str
    listing_url: str
    link_pattern: re.Pattern
    site_domain: str


DEPARTMENTS: dict[str, DepartmentSource] = {
    "ndrc": DepartmentSource(
        name="国家发展和改革委员会",
        # /xxgk/zcfb/ 是 window.location.href 的 JS 跳转壳，真正的列表在跳转目标 fzggwl/ 下
        listing_url="https://www.ndrc.gov.cn/xxgk/zcfb/fzggwl/",
        link_pattern=re.compile(
            r'href="([^"]*t(\d{8})_\d+\.html)"[^>]*>\s*([^<]{6,120})<'
        ),
        site_domain="ndrc.gov.cn",
    ),
    "miit": DepartmentSource(
        name="工业和信息化部",
        # /zwgk/zcwj/wjfb/ 下的深层列表页是纯 JS 空壳；首页服务端渲染了政策文件库最新预览
        listing_url="https://www.miit.gov.cn/",
        link_pattern=re.compile(
            r'href="(https?://www\.miit\.gov\.cn/zwgk/zcwj/wjfb/\w+/art/(\d{4})/'
            r'art_[0-9a-f]+\.html)"[^>]*>\s*([^<]{6,120})<'
        ),
        site_domain="miit.gov.cn",
    ),
    "mem": DepartmentSource(
        name="应急管理部",
        listing_url="https://www.mem.gov.cn/gk/tzgg/",
        link_pattern=re.compile(
            r'href="([^"]*t(\d{8})_\d+\.shtml)"[^>]*>\s*([^<]{6,120})<'
        ),
        site_domain="mem.gov.cn",
    ),
}

ASSOCIATED_DOMAINS: dict[str, tuple[str, str]] = {
    "gov": ("国务院", "gov.cn"),
    "mee": ("生态环境部", "mee.gov.cn"),
    "samr": ("市场监管总局", "samr.gov.cn"),
    "nea": ("国家能源局", "nea.gov.cn"),
    "mof": ("财政部", "mof.gov.cn"),
}


def resolve_site_domain(dept_code: str) -> str | None:
    """返回用于 site: 限定的域名，未知代码返回 None。"""
    if dept_code in DEPARTMENTS:
        return DEPARTMENTS[dept_code].site_domain
    if dept_code in ASSOCIATED_DOMAINS:
        return ASSOCIATED_DOMAINS[dept_code][1]
    return None


def resolve_display_name(dept_code: str) -> str:
    """返回中文显示名，未知代码原样返回代码本身。"""
    if dept_code in DEPARTMENTS:
        return DEPARTMENTS[dept_code].name
    if dept_code in ASSOCIATED_DOMAINS:
        return ASSOCIATED_DOMAINS[dept_code][0]
    return dept_code
