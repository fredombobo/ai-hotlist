"""Collector: 媒体 / 博客 RSS（AI 相关性硬校验）"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from time import mktime

import feedparser
import requests

from src.models import HotItem, clean_text

SOURCE_LABEL = "媒体"

# 广告 / 促销 / 会议软文特征词 —— 一律丢弃
SPAM_PATTERNS = [
    r"lock in your", r"save up to", r"pass savings", r"register now",
    r"early[- ]bird", r"tickets? (are )?(now )?(on sale|available)",
    r"starts in \d+ days", r"join us (at|for)", r"don't miss",
    r"sponsor", r"webinar", r"podcast episode", r"newsletter",
    r"price(s)? (rise|increase)", r"discount", r"promo",
]
_SPAM_RE = re.compile("|".join(SPAM_PATTERNS), re.IGNORECASE)

# AI 相关性：命中其一才算 AI 内容
AI_KEYWORDS = [
    "ai", "llm", "gpt", "claude", "gemini", "llama", "mistral", "qwen",
    "agent", "neural", "machine learning", "deep learning", "transformer",
    "diffusion", "rag", "embedding", "inference", "fine-tun", "prompt",
    "openai", "anthropic", "hugging face", "huggingface", "copilot",
    "multimodal", "chatbot", "model", "training", "dataset", "benchmark",
    "人工智能", "大模型", "智能体",
]
_AI_WORD_RE = re.compile(
    r"(?<![a-z])(" + "|".join(re.escape(k) for k in AI_KEYWORDS) + r")(?![a-z])",
    re.IGNORECASE,
)


def _is_ai_related(title: str, summary: str, source_name: str) -> bool:
    """判断是否 AI 相关。

    规则：
      - 垂类源（名字含 AI / HuggingFace / OpenAI）：默认放行
      - 泛科技源（HN / TechCrunch / Reuters…）：标题必须命中 AI 词
      - 标题里出现「社会新闻特征词」（犯罪、诉讼、选举…）一律丢弃
    """
    # 社会新闻 / 非技术议题特征词 —— 即使含 "AI" 也丢
    SOCIAL_NOISE = re.compile(
        r"\b(murder|killer|sentence|judge|court|lawsuit|election|senator|"
        r"president|congress|immigration|war|sanction|nobel|peace prize|"
        r"police|arrest|trial|verdict|ceasefire)\b",
        re.IGNORECASE,
    )
    if SOCIAL_NOISE.search(title):
        return False

    strict_sources = ["hacker news", "techcrunch", "the verge", "reuters",
                      "bloomberg", "wsj", "nbc"]
    is_strict = any(s in source_name.lower() for s in strict_sources)

    if not is_strict:
        return True

    # 严格源：标题必须命中 AI 词（摘要太容易被噪音污染）
    return bool(_AI_WORD_RE.search(title))


def collect(cfg: dict) -> list[HotItem]:
    m_cfg = cfg.get("sources", {}).get("media_rss", {})
    if not m_cfg.get("enabled", True):
        return []

    feeds = m_cfg.get("feeds", [])
    max_items = m_cfg.get("max_items", 6)

    items: list[HotItem] = []
    seen: set[str] = set()
    stats = {"spam": 0, "offtopic": 0, "kept": 0}

    for feed_info in feeds:
        if len(items) >= max_items * 3:
            break
        name = feed_info.get("name", "Unknown")
        url = feed_info.get("url", "")
        if not url:
            continue
        try:
            r = requests.get(url, timeout=8,
                             headers={"User-Agent": "Mozilla/5.0 (compatible; AIHotlist/1.0)"})
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as e:
            print(f"      ! {name} 拉取失败: {str(e)[:70]}", flush=True)
            continue

        for entry in feed.entries[:20]:
            link = entry.get("link", "")
            if not link or link in seen:
                continue

            # 排掉 HN 转载的 GitHub 仓库页（那是「新项目」板块的内容，不该混进媒体组）
            if "github.com" in link:
                continue

            title = clean_text(entry.get("title", ""), 200)
            if not title or len(title) < 8:
                continue

            # ---- 过滤 1：广告 / 促销 ----
            if _SPAM_RE.search(title):
                stats["spam"] += 1
                continue

            raw_summary = entry.get("summary", "") or entry.get("description", "")
            summary = clean_text(raw_summary, 320)

            # ---- 过滤 2：AI 相关性 ----
            if not _is_ai_related(title, summary, name):
                stats["offtopic"] += 1
                continue

            published = None
            for field in ("published_parsed", "updated_parsed"):
                tp = entry.get(field)
                if tp:
                    try:
                        published = datetime.fromtimestamp(mktime(tp), tz=timezone.utc)
                    except Exception:
                        pass
                    break

            # HN 有 points
            raw_score = 0.0
            if "hnrss" in url or "news.ycombinator" in link:
                try:
                    raw_score = float(entry.get("points") or 0)
                except Exception:
                    raw_score = 0.0
            if not raw_score:
                raw_score = 50.0

            seen.add(link)
            items.append(HotItem(
                title=title,
                url=link,
                source="media_rss",
                source_label=SOURCE_LABEL,
                summary=summary,
                raw_score=raw_score,
                author=entry.get("author", name),
                published_at=published,
                lang="en",
                extra={"source_name": name},
            ))
            stats["kept"] += 1

    print(f"      媒体源：保留 {stats['kept']}，丢弃广告 {stats['spam']}，"
          f"丢弃非 AI {stats['offtopic']}", flush=True)

    items.sort(key=lambda x: (x.published_at or datetime(1970, 1, 1, tzinfo=timezone.utc)),
               reverse=True)
    return items[:max_items]
