"""
简报渲染器

产出两样东西：
  1. docs/brief.txt  — 纯文本预览（= 企业微信 markdown 的实际观感）
  2. 供 HTML 生成器复用的 sections 结构

设计：按来源分组，每组内部按热度排序。
分组标题用「价值语义」而非「来源名」，让读者知道该组是干什么的。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from src.models import HotItem

CST = timezone(timedelta(hours=8))

GROUP_META = {
    "media_rss": {"icon": "📰", "title": "值得一读", "hint": "媒体解读与观点"},
    "github":    {"icon": "📦", "title": "新项目",   "hint": "GitHub 热门仓库"},
    "arxiv":     {"icon": "📄", "title": "论文速览", "hint": "有工程价值的论文"},
}


def _fmt_time(dt) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CST).strftime("%m-%d %H:%M")


def render_brief(groups: dict[str, list[HotItem]], total: int,
                 dry: bool = False) -> str:
    """渲染纯文本简报（企业微信 markdown 风格）。"""
    now = datetime.now(CST)
    L: list[str] = []

    L.append(f"🔥 **AI 热点日报** · {now:%m/%d}")
    stat = " | ".join(
        f"{GROUP_META[s]['title']} {len(v)}" for s, v in groups.items() if v
    )
    L.append(f"<font color=\"comment\">{stat}</font>")
    L.append("")

    for src, items in groups.items():
        if not items:
            continue
        meta = GROUP_META.get(src, {"icon": "🔗", "title": src, "hint": ""})
        L.append(f"**{meta['icon']} {meta['title']}** · {meta['hint']}")
        L.append("")

        for i, it in enumerate(items, 1):
            # 标题行：中文提要优先，没有就用原标题
            headline = it.brief or it.title
            L.append(f"{i}. **{headline}**")

            # 补充行：原始标题（当提要与标题差异大时才显示）
            if it.brief and it.brief != it.title:
                extra_bits = []
                if src == "github" and it.extra.get("stars"):
                    extra_bits.append(f"⭐{it.extra['stars']}")
                    extra_bits.append(it.extra.get("language", ""))
                elif src == "media_rss":
                    extra_bits.append(it.extra.get("source_name", ""))
                elif src == "arxiv":
                    cats = it.extra.get("categories", [])[:2]
                    if cats:
                        extra_bits.append("/".join(cats))
                extra_bits.append(_fmt_time(it.published_at))
                tail = " · ".join(b for b in extra_bits if b)
                L.append(f"   <font color=\"comment\">{it.title[:70]}</font>")
                if tail:
                    L.append(f"   <font color=\"comment\">{tail}</font>")
            else:
                extra_bits = [_fmt_time(it.published_at)]
                if it.extra.get("stars"):
                    extra_bits.insert(0, f"⭐{it.extra['stars']}")
                L.append(f"   <font color=\"comment\">{' · '.join(b for b in extra_bits if b)}</font>")

            L.append(f"   [阅读原文]({it.url})")
            L.append("")

    L.append("---")
    L.append('<font color="comment">完整页面 '
             '[点击查看](https://fredombobo.github.io/ai-hotlist/)</font>')

    return "\n".join(L)


def build_sections(groups: dict[str, list[HotItem]]) -> list[dict]:
    """给 HTML 模板用的分组结构。"""
    sections = []
    for src, items in groups.items():
        if not items:
            continue
        meta = GROUP_META.get(src, {"icon": "🔗", "title": src, "hint": ""})
        sections.append({
            "source": src,
            "label": meta["title"],
            "icon": meta["icon"],
            "hint": meta["hint"],
            "articles": items,
            "fmt_time": _fmt_time,
        })
    return sections
