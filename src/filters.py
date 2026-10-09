"""
过滤 & 排序引擎

核心设计：**分数只在同源内比较**。
每个来源先做 min-max 归一化到 0-100，再乘上组间权重用于全局排序。
这样「GitHub star 数」和「arXiv 关键词命中数」不再直接对撞。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from src.models import HotItem


class HotFilter:
    def __init__(self, cfg: dict):
        f = cfg.get("filter", {})
        self.recent_hours = f.get("recent_hours", 48)
        self.max_total = f.get("max_total", 60)
        self.source_weight = f.get("source_weight", {})
        self.brief_per_source = f.get("brief_per_source", {})

    # ------------------------------------------------ 时效
    def filter_fresh(self, items: list[HotItem]) -> list[HotItem]:
        if self.recent_hours <= 0:
            return items
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.recent_hours)
        out = []
        for it in items:
            if it.published_at is None:
                continue
            pub = it.published_at
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            if pub >= cutoff:
                out.append(it)
        return out

    # ------------------------------------------------ 去重
    def deduplicate(self, items: list[HotItem]) -> list[HotItem]:
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        out: list[HotItem] = []
        for it in items:
            if it.url in seen_urls:
                continue
            key = "".join(ch for ch in it.title.lower() if ch.isalnum())[:50]
            if key and key in seen_titles:
                continue
            seen_urls.add(it.url)
            if key:
                seen_titles.add(key)
            out.append(it)
        return out

    # ------------------------------------------------ 组内归一化
    def normalize_within_source(self, items: list[HotItem]) -> list[HotItem]:
        """按来源分组，组内 min-max 归一化到 0-100。

        解决三个问题：
          1. 跨源分数不可比 → 归一化后各组上限一致
          2. 天花板粘连（原来 5 条都是 100）→ 归一化后展开
          3. 组间仍需全局排序 → 用 source_weight 加权
        """
        by_source: dict[str, list[HotItem]] = {}
        for it in items:
            by_source.setdefault(it.source, []).append(it)

        for src, group in by_source.items():
            vals = [x.raw_score for x in group]
            lo, hi = min(vals), max(vals)
            span = hi - lo
            for x in group:
                if span > 0:
                    norm = (x.raw_score - lo) / span * 100.0
                else:
                    norm = 80.0   # 组内全相同 → 给个中性高分
                w = self.source_weight.get(src, 1.0)
                x.score = round(min(norm * w, 100.0), 1)

        return items

    # ------------------------------------------------ 主流水线
    def filter_and_rank(self, items: list[HotItem]) -> list[HotItem]:
        items = self.filter_fresh(items)
        items = self.deduplicate(items)
        items = self.normalize_within_source(items)
        items.sort(key=lambda x: x.score, reverse=True)
        return items[: self.max_total]

    # ------------------------------------------------ 简报刊选
    def pick_for_brief(self, items: list[HotItem]) -> dict[str, list[HotItem]]:
        """按来源挑选进入简报的条目，每组独立排序。"""
        out: dict[str, list[HotItem]] = {}
        order = ["media_rss", "github", "arxiv"]
        for src in order:
            group = [x for x in items if x.source == src]
            n = self.brief_per_source.get(src, 3)
            if group:
                out[src] = group[:n]
        return out


def run_filter(items: list[HotItem], cfg: dict) -> list[HotItem]:
    return HotFilter(cfg).filter_and_rank(items)
