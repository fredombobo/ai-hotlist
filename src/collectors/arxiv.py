"""Collector: arXiv（偏向有工程价值的论文）"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from time import mktime

import feedparser
import requests

from src.models import HotItem, clean_text

SOURCE_LABEL = "论文"
API = "http://export.arxiv.org/api/query"


def collect(cfg: dict) -> list[HotItem]:
    a_cfg = cfg.get("sources", {}).get("arxiv", {})
    if not a_cfg.get("enabled", True):
        return []

    cats = a_cfg.get("categories", ["cs.AI"])
    max_items = a_cfg.get("max_items", 4)
    recent_days = a_cfg.get("recent_days", 2)
    prefer = [k.lower() for k in a_cfg.get("prefer_keywords", [])]
    exclude = [k.lower() for k in a_cfg.get("exclude_keywords", [])]

    items: list[HotItem] = []
    seen: set[str] = set()

    for cat in cats:
        try:
            params = {
                "search_query": f"cat:{cat}",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": 50,
            }
            r = requests.get(API, params=params, timeout=10)
            if r.status_code != 200:
                continue
            feed = feedparser.parse(r.content)
            if feed.bozo:
                continue
        except Exception:
            continue

        for entry in feed.entries:
            aid = entry.get("id", "")
            if not aid or aid in seen:
                continue

            title = clean_text(entry.get("title", ""), 200)
            summary = clean_text(entry.get("summary", ""), 300)
            if not title:
                continue

            text = (title + " " + summary).lower()

            # 排除不相关领域
            if any(k in text for k in exclude):
                continue

            # 时效
            published = None
            tp = entry.get("published_parsed")
            if tp:
                try:
                    published = datetime.fromtimestamp(mktime(tp), tz=timezone.utc)
                except Exception:
                    pass
            if published:
                if datetime.now(timezone.utc) - published > timedelta(days=recent_days):
                    continue

            # 相关性打分：命中 prefer 关键词的数量（命中多者优先排序）
            # 注意：不命中不再直接丢弃 —— 否则周末/冷门方向会导致整组为空
            hits = sum(1 for k in prefer if k in text)

            seen.add(aid)
            items.append(HotItem(
                title=title,
                url=entry.get("link", aid),
                source="arxiv",
                source_label=SOURCE_LABEL,
                summary=summary,
                raw_score=float(hits) + 1.0,   # +1 保证未命中的也有基础分
                author=", ".join(
                    a.get("name", "") for a in entry.get("authors", [])[:3]
                ),
                published_at=published,
                lang="en",
                tags=[t.get("term", "") for t in entry.get("tags", [])][:3],
                extra={"categories": [t.get("term", "") for t in entry.get("tags", [])]},
            ))

    items.sort(key=lambda x: x.raw_score, reverse=True)
    return items[:max_items]
