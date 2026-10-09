"""
数据模型 & 配置加载
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import yaml


# ---------------------------------------------------------------- 清洗工具

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ABSTRACT_PREFIX_RE = re.compile(
    r"^\s*(abstract|摘要)\s*[:：\-—]?\s*", re.IGNORECASE
)
_ARXIV_NOISE_RE = re.compile(
    r"(this paper (presents|proposes|introduces|studies)|we (present|propose|introduce|study)|"
    r"in this paper,?\s*)",
    re.IGNORECASE,
)


def clean_text(raw: str, limit: int = 0) -> str:
    """清洗摘要文本：去 HTML、去 arXiv 噪音前缀、折叠空白、可选截断。"""
    if not raw:
        return ""
    t = _TAG_RE.sub(" ", raw)
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") \
         .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    t = _ABSTRACT_PREFIX_RE.sub("", t)
    t = _WS_RE.sub(" ", t).strip()
    if limit and len(t) > limit:
        t = _truncate_sentence(t, limit)
    return t


def _truncate_sentence(text: str, limit: int) -> str:
    """在句子边界截断，避免出现 'compresses it' 这种断尾。"""
    if len(text) <= limit:
        return text
    window = text[:limit]
    # 优先在句末标点处断
    for punct in (". ", "。", "! ", "? ", "; ", "；"):
        idx = window.rfind(punct)
        if idx > limit * 0.5:
            return window[: idx + 1].strip()
    # 退而求其次在词边界断
    idx = window.rfind(" ")
    if idx > limit * 0.6:
        return window[:idx].strip() + "…"
    return window.strip() + "…"


# ---------------------------------------------------------------- 数据模型

@dataclass
class HotItem:
    """一条热点条目"""
    title: str
    url: str
    source: str                     # github / media_rss / arxiv
    source_label: str
    summary: str = ""               # 清洗后的原文摘要
    brief: str = ""                 # LLM 生成的中文提要（一行）
    score: float = 0.0              # 归一化后的组内相对热度 0-100
    raw_score: float = 0.0          # 源原始热度（star/upvote/…）
    author: str = ""
    published_at: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    lang: str = "en"                # en / zh

    @property
    def age_hours(self) -> float:
        if not self.published_at:
            return 0.0
        delta = datetime.now(self.published_at.tzinfo) - self.published_at
        return max(0.0, delta.total_seconds() / 3600)


# ---------------------------------------------------------------- 配置

def load_config(path: str = "config.yaml") -> dict:
    """加载 YAML 配置，环境变量覆盖。"""
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 企业微信 key 优先走环境变量
    wecom_key = os.environ.get("WECOM_WEBHOOK_KEY", "")
    if wecom_key:
        cfg.setdefault("wecom", {})["webhook_url"] = (
            f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={wecom_key}"
        )

    # GitHub Token
    gh_token = os.environ.get("GH_PAT", "")
    if gh_token:
        cfg["github_token"] = gh_token

    # LLM 配置（用于生成中文提要）
    llm = cfg.setdefault("llm", {})
    for env_key, cfg_key in (
        ("LLM_API_KEY", "api_key"),
        ("LLM_BASE_URL", "base_url"),
        ("LLM_MODEL", "model"),
    ):
        v = os.environ.get(env_key, "")
        if v:
            llm[cfg_key] = v

    return cfg
