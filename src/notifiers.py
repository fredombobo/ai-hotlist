"""
企业微信推送（默认关闭）

注意：推送内容直接复用 brief.py 渲染好的简报文本，
不再单独拼格式 —— 保证「网页预览 == 实际推送」，所见即所得。
"""
from __future__ import annotations

import requests


def push(cfg: dict, brief_text: str):
    webhook_url = cfg.get("wecom", {}).get("webhook_url", "")
    if not webhook_url or not brief_text:
        return False

    # 企业微信 markdown 有长度上限，超长时截断到最后一个完整条目
    MAX = 3900
    content = brief_text
    if len(content) > MAX:
        content = content[:MAX].rsplit("\n\n", 1)[0]
        content += "\n\n<font color=\"comment\">…内容过长已截断，完整版见网页</font>"

    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    try:
        sess = requests.Session()
        sess.trust_env = False      # 企业微信 API 需直连，绕开代理
        r = sess.post(webhook_url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception:
        return False
