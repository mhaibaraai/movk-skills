#!/usr/bin/env python3
"""
部委检索配置。纯数据，不含请求逻辑。

两个检索源：

  政策文件库  国务院政策文件库的检索接口，覆盖全部部委，支持关键词检索。
              library_type 选 zhengcelibrary_bm（部门文件）或 zhengcelibrary_gw（国务院文件）。
              接口不支持按发文机关过滤，需在客户端用 puborg_keys 子串匹配 puborg 字段。
              实测 puborg 用简称且可能带内设机构后缀（国家发展改革委 / 工业和信息化部办公厅 /
              能源局综合司），所以匹配键取核心词而非全称。puborg_keys 为空表示不过滤。

  官网列表页  部委官网的最新文件列表，只有最新 N 条、不支持关键词检索，但能覆盖政策文件库
              不收录的征求意见稿与通报。仅为核实过抓取规则的部委配置 listing。
              link_pattern 从原始 HTML 提取 (href, date_hint, title)；date_hint 为
              8 位 YYYYMMDD 或 4 位 YYYY。

site_domain 不参与脚本检索，在检索失败时回传给调用方，供其构造 site: 限定的网页搜索。
"""

import re
from dataclasses import dataclass

POLICY_LIBRARY_URL = "https://sousuo.www.gov.cn/search-gov/data"

LIBRARY_DEPARTMENT = "zhengcelibrary_bm"
LIBRARY_STATE_COUNCIL = "zhengcelibrary_gw"


@dataclass(frozen=True)
class ListingSource:
    url: str
    link_pattern: re.Pattern


@dataclass(frozen=True)
class Department:
    name: str
    site_domain: str
    puborg_keys: tuple[str, ...]
    library_type: str = LIBRARY_DEPARTMENT
    listing: ListingSource | None = None


DEPARTMENTS: dict[str, Department] = {
    "ndrc": Department(
        name="国家发展和改革委员会",
        site_domain="ndrc.gov.cn",
        puborg_keys=("发展改革委",),
        listing=ListingSource(
            # /xxgk/zcfb/ 是 window.location.href 的 JS 跳转壳，真正的列表在跳转目标 fzggwl/ 下
            url="https://www.ndrc.gov.cn/xxgk/zcfb/fzggwl/",
            link_pattern=re.compile(
                r'href="([^"]*t(\d{8})_\d+\.html)"[^>]*>\s*([^<]{6,120})<'
            ),
        ),
    ),
    "miit": Department(
        name="工业和信息化部",
        site_domain="miit.gov.cn",
        puborg_keys=("工业和信息化部",),
        listing=ListingSource(
            # /zwgk/zcwj/wjfb/ 下的深层列表页是纯 JS 空壳；首页服务端渲染了政策文件库最新预览
            url="https://www.miit.gov.cn/",
            link_pattern=re.compile(
                r'href="(https?://www\.miit\.gov\.cn/zwgk/zcwj/wjfb/\w+/art/(\d{4})/'
                r'art_[0-9a-f]+\.html)"[^>]*>\s*([^<]{6,120})<'
            ),
        ),
    ),
    "mem": Department(
        name="应急管理部",
        site_domain="mem.gov.cn",
        puborg_keys=("应急管理部",),
        listing=ListingSource(
            url="https://www.mem.gov.cn/gk/tzgg/",
            link_pattern=re.compile(
                r'href="([^"]*t(\d{8})_\d+\.shtml)"[^>]*>\s*([^<]{6,120})<'
            ),
        ),
    ),
    "gov": Department(
        name="国务院",
        site_domain="gov.cn",
        puborg_keys=(),
        library_type=LIBRARY_STATE_COUNCIL,
    ),
    "mee": Department(
        name="生态环境部",
        site_domain="mee.gov.cn",
        puborg_keys=("生态环境部",),
    ),
    "samr": Department(
        name="市场监管总局",
        site_domain="samr.gov.cn",
        puborg_keys=("市场监管总局",),
    ),
    "nea": Department(
        name="国家能源局",
        site_domain="nea.gov.cn",
        puborg_keys=("能源局",),
    ),
    "mof": Department(
        name="财政部",
        site_domain="mof.gov.cn",
        puborg_keys=("财政部",),
    ),
}


def resolve_site_domain(dept_code: str) -> str:
    """返回用于 site: 限定的域名，未知代码返回空串。"""
    dept = DEPARTMENTS.get(dept_code)
    return dept.site_domain if dept else ""


def resolve_display_name(dept_code: str) -> str:
    """返回中文显示名，未知代码原样返回代码本身。"""
    dept = DEPARTMENTS.get(dept_code)
    return dept.name if dept else dept_code
