"""Collector: GitHub 仓库（收紧查询 + 时效过滤 + 描述校验）

性能要点：绝不对单个仓库调 get_topics() —— 那会对每个仓库发一次 API 请求，
50 个仓库就把限额打爆并触发 PyGithub 的分钟级退避。
改用 search API 的 query 里带 topic 过滤，直接从结果字段取。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import urllib.error

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
            remaining = r.headers.get("X-RateLimit-Remaining", "?")
            print(f"      · search OK (remaining={remaining})", flush=True)
            return json.loads(r.read().decode()).get("items", [])
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        print(f"      ! GitHub HTTP {e.code}: {body}", flush=True)
        return []
    except Exception as e:
        print(f"      ! GitHub 查询失败: {str(e)[:90]}", flush=True)
        return []


def _core_repo(token: str, full_name: str) -> dict | None:
    """用 core API（5000/h）取单个仓库详情。"""
    import json
    import urllib.request

    req = urllib.request.Request(
        f"https://api.github.com/repos/{full_name}", headers=_gh_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _trending_fallback() -> list[dict]:
    """降级方案：抓 GitHub Trending 页面（不消耗任何 API 配额）。

    仅在 search API 配额耗尽（CI 环境常见）时启用。
    """
    import re
    import urllib.request

    out: list[dict] = []
    try:
        req = urllib.request.Request(
            "https://github.com/trending?since=daily",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"      ! trending 也失败: {str(e)[:70]}", flush=True)
        return []

    # 解析 <article class="Box-row"> 里的仓库链接
    for m in re.finditer(
        r'<h2 class="h3 lh-condensed">\s*<a href="/([^"/]+)/([^"]+)"', html
    ):
        owner, name = m.group(1), m.group(2)
        full = f"{owner}/{name}"
        out.append({
            "full_name": full,
            "html_url": f"https://github.com/{full}",
            "description": "",          # trending 页描述需另解析，留空交给后续校验
            "topics": [],
            "stargazers_count": 0,
            "forks_count": 0,
            "language": None,
            "pushed_at": None,
            "_from_trending": True,
        })
    print(f"      · trending 兜底：{len(out)} 个仓库", flush=True)
    return out


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
    search_failed = False

    for query in queries:
        if len(items) >= max_items * 3:
            break
        q = f"{query} stars:>={min_stars} pushed:>={cutoff_s}"
        repos = _search(token, q)
        if not repos:
            search_failed = True
            continue

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

    # ---- 兜底：search 全失败时改用 Trending 页 + core API 补详情 ----
    if search_failed and not items:
        print("      ! search 全部失败，切换到 trending 兜底", flush=True)
        for repo in _trending_fallback()[: max_items * 2]:
            full = repo["full_name"]
            if full in seen:
                continue
            # 用 core API 补描述（core 限额 5000/h，不受 search 30/h 限制）
            detail = _core_repo(token, full)
            if detail:
                desc = (detail.get("description") or "").strip()
                topics = detail.get("topics") or []
                stars = detail.get("stargazers_count", 0)
            else:
                desc, topics, stars = "", [], 0

            haystack = (desc + " " + " ".join(topics) + " " + full).lower()
            if require_kw and not any(k in haystack for k in require_kw):
                continue
            if len(desc) < 15:
                continue

            pushed_s = (detail or {}).get("pushed_at")
            try:
                pushed = datetime.fromisoformat(
                    pushed_s.replace("Z", "+00:00")) if pushed_s \
                    else datetime.now(timezone.utc)
            except Exception:
                pushed = datetime.now(timezone.utc)

            seen.add(full)
            items.append(HotItem(
                title=full,
                url=repo["html_url"],
                source="github",
                source_label=SOURCE_LABEL,
                summary=clean_text(desc, 220),
                raw_score=float(stars) if stars else 1000.0,
                author=full.split("/")[0],
                published_at=pushed,
                tags=topics[:3],
                lang="en",
                extra={
                    "stars": stars,
                    "forks": (detail or {}).get("forks_count", 0),
                    "language": (detail or {}).get("language") or "unknown",
                    "topics": topics[:6],
                },
            ))

    items.sort(key=lambda x: x.raw_score, reverse=True)
    print(f"      GitHub：{len(items)} 条候选", flush=True)
    return items[:max_items]
