"""Collector: GitHub 仓库（收紧查询 + 时效过滤 + 描述校验）

性能要点：绝不对单个仓库调 get_topics() —— 那会对每个仓库发一次 API 请求，
50 个仓库就把限额打爆并触发 PyGithub 的分钟级退避。
改用 search API 的 query 里带 topic 过滤，直接从结果字段取。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from src.models import HotItem, clean_text

SOURCE_LABEL = "GitHub"
API = "https://api.github.com/search/repositories"


def _gh_headers(token: str) -> dict:
    h = {"Accept": "application/vnd.github+json",
         "User-Agent": "ai-hotlist"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _search(token: str, q: str, per_page: int = 30) -> list[dict]:
    """直接打 REST API，避免 PyGithub 的额外请求与退避。"""
    import json
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode({
        "q": q, "sort": "stars", "order": "desc", "per_page": per_page,
    })
    req = urllib.request.Request(f"{API}?{params}", headers=_gh_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode()).get("items", [])
    except Exception as e:
        print(f"      ! GitHub 查询失败: {str(e)[:90]}", flush=True)
        return []


def collect(cfg: dict) -> list[HotItem]:
    gh_cfg = cfg.get("sources", {}).get("github", {})
    if not gh_cfg.get("enabled", True):
        return []

    queries = gh_cfg.get("queries", ["topic:llm"])
    max_items = gh_cfg.get("max_items", 8)
    min_stars = gh_cfg.get("min_stars", 0)
    pushed_days = gh_cfg.get("pushed_within_days", 7)
    require_kw = [k.lower() for k in gh_cfg.get("require_keywords", [])]

    token = cfg.get("github_token", "") or ""
    cutoff = datetime.now(timezone.utc) - timedelta(days=pushed_days)
    cutoff_s = cutoff.strftime("%Y-%m-%d")

    items: list[HotItem] = []
    seen: set[str] = set()

    for query in queries:
        if len(items) >= max_items * 3:
            break
        q = f"{query} stars:>={min_stars} pushed:>={cutoff_s}"
        repos = _search(token, q)

        for repo in repos:
            full = repo.get("full_name", "")
            html_url = repo.get("html_url", "")
            if not full or html_url in seen:
                continue

            desc = (repo.get("description") or "").strip()
            topics = repo.get("topics") or []
            stars = repo.get("stargazers_count", 0)

            # 校验 1：描述必须命中 AI 关键词
            haystack = (desc + " " + " ".join(topics) + " " + full).lower()
            if require_kw and not any(k in haystack for k in require_kw):
                continue

            # 校验 2：星数
            if stars < min_stars:
                continue

            # 校验 3：pushed_at 在窗口内
            pushed_s = repo.get("pushed_at")
            if pushed_s:
                try:
                    pushed = datetime.fromisoformat(pushed_s.replace("Z", "+00:00"))
                except Exception:
                    pushed = datetime.now(timezone.utc)
                if pushed < cutoff:
                    continue
            else:
                pushed = datetime.now(timezone.utc)

            # 校验 4：描述要有实质内容
            if len(desc) < 15:
                continue

            seen.add(html_url)
            items.append(HotItem(
                title=full,
                url=html_url,
                source="github",
                source_label=SOURCE_LABEL,
                summary=clean_text(desc, 220),
                raw_score=float(stars),
                author=full.split("/")[0],
                published_at=pushed,
                tags=[t for t in topics[:3]],
                lang="en",
                extra={
                    "stars": stars,
                    "forks": repo.get("forks_count", 0),
                    "language": repo.get("language") or "unknown",
                    "topics": topics[:6],
                },
            ))
            if len(items) >= max_items * 3:
                break

    items.sort(key=lambda x: x.raw_score, reverse=True)
    print(f"      GitHub：{len(items)} 条候选", flush=True)
    return items[:max_items]
