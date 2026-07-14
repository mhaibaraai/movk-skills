#!/usr/bin/env python3
"""
石油化工企业与行业研究机构元数据。纯数据，不含请求逻辑。

发现候选统一走 web-fetch 基座的 360 搜索（--site 用 site_domain 限定），
不再为每家机构手写官网列表页抓取正则——各家官网改版频繁，维护一堆正则性价比
太低；360 搜索本身也能命中企业官网之外的第三方披露/转载源（如新浪财经转载的
年报页），覆盖面反而更稳。

抓取侧由 web-fetch 的四层引擎负责：国内企业官网多有反爬（中国石油是瑞数 JS
挑战，HTTP 412；中国海油是 JS 空壳），靠 reader_proxy/playwright 层拿正文，
不需要本模块操心。

CLI:
  uv run scripts/sources.py --list          打印全部机构（校验数据完整性）
  uv run scripts/sources.py --show sinopec  打印单条机构详情
"""
import argparse
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Source:
    code: str
    name_zh: str
    name_en: str
    site_domain: str                 # 供 web-fetch search.py 的 --site 限定
    org_type: str                     # soe_domestic / intl_major / research_institute
    language: str                     # zh / en
    report_types: tuple[str, ...]     # 语义提示，不参与脚本逻辑


SOURCES: dict[str, Source] = {
    "sinopec": Source(
        code="sinopec", name_zh="中国石化", name_en="Sinopec",
        site_domain="sinopec.com", org_type="soe_domestic", language="zh",
        report_types=("年度报告", "可持续发展报告", "新闻稿"),
    ),
    "petrochina": Source(
        code="petrochina", name_zh="中国石油", name_en="PetroChina / CNPC",
        site_domain="cnpc.com.cn", org_type="soe_domestic", language="zh",
        report_types=("年度报告", "可持续发展报告", "新闻稿"),
    ),
    "cnooc": Source(
        code="cnooc", name_zh="中国海油", name_en="CNOOC",
        site_domain="cnooc.com.cn", org_type="soe_domestic", language="zh",
        report_types=("年度报告", "可持续发展报告", "新闻稿"),
    ),
    "sinochem": Source(
        code="sinochem", name_zh="中化集团", name_en="Sinochem",
        site_domain="sinochem.com", org_type="soe_domestic", language="zh",
        report_types=("年度报告", "新闻稿"),
    ),
    "yanchang": Source(
        code="yanchang", name_zh="延长石油", name_en="Yanchang Petroleum",
        site_domain="ycpg.cn", org_type="soe_domestic", language="zh",
        report_types=("年度报告", "新闻稿"),
    ),
    "exxonmobil": Source(
        code="exxonmobil", name_zh="埃克森美孚", name_en="ExxonMobil",
        site_domain="exxonmobil.com", org_type="intl_major", language="en",
        report_types=("年度报告", "可持续发展报告", "新闻稿", "能源展望"),
    ),
    "shell": Source(
        code="shell", name_zh="壳牌", name_en="Shell",
        site_domain="shell.com", org_type="intl_major", language="en",
        report_types=("年度报告", "可持续发展报告", "新闻稿", "能源展望"),
    ),
    "bp": Source(
        code="bp", name_zh="英国石油", name_en="BP",
        site_domain="bp.com", org_type="intl_major", language="en",
        report_types=("年度报告", "可持续发展报告", "新闻稿", "能源展望"),
    ),
    "totalenergies": Source(
        code="totalenergies", name_zh="道达尔能源", name_en="TotalEnergies",
        site_domain="totalenergies.com", org_type="intl_major", language="en",
        report_types=("年度报告", "可持续发展报告", "新闻稿"),
    ),
    "chevron": Source(
        code="chevron", name_zh="雪佛龙", name_en="Chevron",
        site_domain="chevron.com", org_type="intl_major", language="en",
        report_types=("年度报告", "可持续发展报告", "新闻稿"),
    ),
    "cpcif": Source(
        code="cpcif", name_zh="中国石油和化学工业联合会", name_en="CPCIF",
        site_domain="cpcif.org.cn", org_type="research_institute", language="zh",
        report_types=("行业运行报告", "统计数据", "新闻稿"),
    ),
    "iea": Source(
        code="iea", name_zh="国际能源署", name_en="IEA",
        site_domain="iea.org", org_type="research_institute", language="en",
        report_types=("世界能源展望", "研究报告", "月度报告"),
    ),
    "opec": Source(
        code="opec", name_zh="石油输出国组织", name_en="OPEC",
        site_domain="opec.org", org_type="research_institute", language="en",
        report_types=("月度石油市场报告", "年度统计公报"),
    ),
    "woodmac": Source(
        code="woodmac", name_zh="伍德麦肯兹", name_en="Wood Mackenzie",
        site_domain="woodmac.com", org_type="research_institute", language="en",
        report_types=("行业研究报告", "市场洞察"),
    ),
}


def resolve(code: str) -> Source | None:
    return SOURCES.get(code)


def list_all() -> list[Source]:
    return list(SOURCES.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="石化机构元数据")
    parser.add_argument("--list", action="store_true", help="打印全部机构")
    parser.add_argument("--show", help="打印单条机构详情，传机构代码")
    args = parser.parse_args()

    if args.show:
        src = resolve(args.show)
        if src is None:
            known = ", ".join(sorted(SOURCES))
            parser.error(f"未知机构代码 {args.show}；可选: {known}")
        print(json.dumps(asdict(src), ensure_ascii=False, indent=2))
        return

    print(json.dumps([asdict(s) for s in list_all()], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
