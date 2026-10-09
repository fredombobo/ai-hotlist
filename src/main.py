#!/usr/bin/env python3
"""
AI 热点日报 — 主编排器

用法:
    python -m src.main                 # 完整运行
    python -m src.main --no-llm        # 跳过 LLM，用原文摘要
    python -m src.main --dry-run       # 不推送
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models import load_config, HotItem                     # noqa: E402
from src.filters import HotFilter                               # noqa: E402
from src.brief import render_brief                              # noqa: E402
from src.brief_llm import generate_briefs                       # noqa: E402
from src.html_generator import generate                         # noqa: E402
from src.notifiers import push                                  # noqa: E402

COLLECTORS = {
    "github":    "src.collectors.github",
    "media_rss": "src.collectors.media_rss",
    "arxiv":     "src.collectors.arxiv",
}


def main():
    dry_run = "--dry-run" in sys.argv
    no_llm = "--no-llm" in sys.argv
    only = None
    for a in sys.argv[1:]:
        if a.startswith("--sources="):
            only = [s.strip() for s in a.split("=", 1)[1].split(",")]

    cfg = load_config(str(project_root / "config.yaml"))
    if no_llm:
        cfg.setdefault("llm", {})["enabled"] = False

    # ---- 采集 ----
    all_items: list[HotItem] = []
    names = [n for n in COLLECTORS
             if cfg.get("sources", {}).get(n, {}).get("enabled", True)]
    if only:
        names = [n for n in names if n in only]

    # ---- 采集（并发，单个源故障不阻塞全程）----
    import importlib
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run_one(name: str):
        fn = importlib.import_module(COLLECTORS[name]).collect
        return name, fn(cfg)

    all_items: list[HotItem] = []
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {pool.submit(_run_one, n): n for n in names}
        for fut in as_completed(futures, timeout=120):
            name = futures[fut]
            try:
                _, got = fut.result(timeout=1)
                print(f"    ✓ {name}: {len(got)} 条", flush=True)
                all_items.extend(got)
            except Exception as e:
                print(f"    ✗ {name} 失败: {str(e)[:140]}", flush=True)

    # ---- 过滤 + 组内归一化 ----
    print("  → 过滤 / 去重 / 归一化…", flush=True)
    hf = HotFilter(cfg)
    filtered = hf.filter_and_rank(all_items)

    # ---- 简报选条 ----
    groups = hf.pick_for_brief(filtered)
    brief_items = [it for g in groups.values() for it in g]

    # ---- LLM 中文提要 ----
    brief_items = generate_briefs(cfg, brief_items)
    for g in groups.values():
        pass  # brief_items 是同一批对象，就地更新已生效

    # ---- 渲染 ----
    print("  → 渲染…", flush=True)
    brief_text = render_brief(groups, len(filtered))
    generate(cfg, groups, filtered, brief_text)

    # ---- 推送（默认关闭）----
    wecom = cfg.get("wecom", {})
    if not dry_run and wecom.get("enabled") and wecom.get("webhook_url"):
        push(cfg, brief_text)
        print("  ✓ 已推送企业微信", flush=True)
    else:
        print("  - 推送已跳过（wecom.enabled=false）", flush=True)

    print(f"\n✅ 完成：{len(all_items)} 条原始 → {len(filtered)} 条入选 → "
          f"简报展示 {len(brief_items)} 条", flush=True)


if __name__ == "__main__":
    main()
