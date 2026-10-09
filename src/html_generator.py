"""
HTML 页面生成器
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.brief import build_sections, render_brief

CST = timezone(timedelta(hours=8))


def generate(cfg: dict, groups: dict, all_items, brief_text: str):
    out_cfg = cfg.get("output", {})
    html_path = out_cfg.get("html", "docs/index.html")
    json_path = out_cfg.get("json", "docs/data.json")
    brief_path = out_cfg.get("brief", "docs/brief.txt")

    now = datetime.now(CST)
    sections = build_sections(groups)

    stats = [
        {"label": s["label"], "icon": s["icon"], "count": len(s["articles"])}
        for s in sections
    ]

    env = Environment(
        loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("index.html.j2").render(
        sections=sections,
        stats=stats,
        brief_text=brief_text,
        total_items=len(all_items),
        shown_items=sum(len(s["articles"]) for s in sections),
        generated_at=now.strftime("%Y-%m-%d %H:%M"),
    )

    os.makedirs(os.path.dirname(html_path), exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    with open(brief_path, "w", encoding="utf-8") as f:
        f.write(brief_text)

    data = [{
        "title": it.title,
        "brief": it.brief,
        "url": it.url,
        "source": it.source,
        "source_label": it.source_label,
        "summary": it.summary,
        "score": round(it.score, 1),
        "raw_score": it.raw_score,
        "author": it.author,
        "published_at": it.published_at.isoformat() if it.published_at else None,
        "tags": it.tags,
        "extra": it.extra,
    } for it in all_items]

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": now.isoformat(),
            "total": len(data),
            "items": data,
        }, f, ensure_ascii=False, indent=2)

    print(f"  ✓ HTML  → {html_path}")
    print(f"  ✓ 简报  → {brief_path}")
    print(f"  ✓ JSON  → {json_path}")
