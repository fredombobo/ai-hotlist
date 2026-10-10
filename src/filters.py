"""
过滤 & 排序引擎

核心设计：**分数只在同源内比较**。
每个来源先做 min-max 归一化到 0-100，再乘上组间权重用于全局排序。
这样「GitHub star 数」和「arXiv 关键词命中数」不再直接对撞。
"""
from __future__ import annotations

import re
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
        """三层去重：
        1. URL / 标题精确匹配
        2. 「金额+主体」指纹 —— 同一事件的融资/收购报道，
           不同媒体标题措辞差异大但共享核心数字与公司名
        3. 标题词集 Jaccard 相似度 —— 捕获改写标题
        """
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        seen_fingerprints: set[str] = set()
        kept_tokens: list[set[str]] = []
        out: list[HotItem] = []

        STOP = {
            "the", "a", "an", "of", "to", "and", "in", "on", "for", "with",
            "at", "is", "are", "its", "it", "that", "this", "as", "by",
            "from", "how", "what", "why", "new", "can", "will", "be",
        }

        def tokens(text: str) -> set[str]:
            words = re.findall(r"[a-z0-9]+", text.lower())
            return {w for w in words if len(w) > 2 and w not in STOP}

        def fingerprint(it: HotItem) -> set[str]:
            """稀有金额指纹。

            同一事件的融资/收购报道，标题措辞差异再大，也会共享同一个
            金额（$7.5B / 870M / $1.8 billion）。金额在「同一天 + AI 领域」
            这个语境下稀有度极高，实测误伤率可忽略。

            只收有辨识度的金额：
              - billion 级（任意值）：$7.5B / $1.8 billion
              - million 级但 ≥100：870M / $250 million
            避开 「$50M」「$10M」这类高频小额，否则会误伤
            同一天的多起中等融资。
            """
            text = (it.title + " " + it.summary).lower()
            nums = re.findall(r"\$?\s?(\d+(?:\.\d+)?)\s?(billion|million)\b", text)
            nums += re.findall(r"\$(\d+(?:\.\d+)?)\s?([bm])\b", text)
            keys = set()
            for n, u in nums:
                try:
                    val = float(n)
                except ValueError:
                    continue
                unit = "b" if (u and u[0] == "b") else "m"
                if unit == "m" and val < 100:
                    continue        # 小额融资太常见，不足以判定同一事件
                keys.add(f"{n}{unit}")
            return keys

        for it in items:
            if it.url in seen_urls:
                continue
            key = "".join(ch for ch in it.title.lower() if ch.isalnum())[:50]
            if key and key in seen_titles:
                continue

            # 指纹去重：命中同一稀有金额即视为同一事件
            fkeys = fingerprint(it)
            if fkeys and (fkeys & seen_fingerprints):
                continue

            # 词集相似度
            tk = tokens(it.title)
            dup = False
            if len(tk) >= 4:
                for prev in kept_tokens:
                    if not prev:
                        continue
                    j = len(tk & prev) / len(tk | prev)
                    if j >= 0.5:
                        dup = True
                        break
            if dup:
                continue

            seen_urls.add(it.url)
            if key:
                seen_titles.add(key)
            if fkeys:
                seen_fingerprints |= fkeys
            kept_tokens.append(tk)
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
