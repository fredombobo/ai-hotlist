"""
LLM 中文提要生成

把每条热点的「标题 + 原文摘要」压成一句 30 字以内的中文提要，
让简报从「机器汇总」变成「编辑推荐」。

设计要点：
  - 一次请求批量处理，省 token、省时间
  - 失败时降级为原文截取，绝不阻塞主流程
  - 严格 JSON 输出解析 + 兜底正则提取
"""
from __future__ import annotations

import json
import re
import requests

from src.models import HotItem

SYSTEM_PROMPT = """你是一个中文科技媒体的资深编辑，为「AI 热点日报」撰写提要。

要求：
1. 每条提要用**一句中文**说清三件事：这是什么 / 为什么值得关注
2. 长度控制在 35 个汉字以内，不要用「本文」「该研究」这类主语
3. 说人话，不要直译英文术语；专有名词保留英文原名（如 Claude、RAG）
4. 不要用「值得关注」「引发热议」「重磅」这类空话
5. 不要重复标题里已有的信息，要补充标题没说的关键点
6. 只输出 JSON 数组，不要任何解释文字

输出格式（严格）：
[{"i": 1, "brief": "中文提要"}, {"i": 2, "brief": "..."}]"""


def _build_user_prompt(items: list[HotItem]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(
            f'{i}. 来源={it.source_label} | 标题={it.title}\n'
            f'   原文摘要={(it.summary or "(无)")[:400]}'
        )
    return "请为以下条目各写一句中文提要：\n\n" + "\n".join(lines)


def _parse_response(text: str, n: int) -> list[str]:
    """从 LLM 返回里抠出 brief 数组，容错优先。"""
    text = text.strip()
    # 去掉 markdown 代码围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # 直接尝试 JSON
    try:
        data = json.loads(text)
        if isinstance(data, list):
            out = [""] * n
            for row in data:
                if isinstance(row, dict) and "i" in row and "brief" in row:
                    idx = int(row["i"]) - 1
                    if 0 <= idx < n:
                        out[idx] = str(row["brief"]).strip()
            return out
    except Exception:
        pass

    # 兜底：按序号抓
    out = [""] * n
    for m in re.finditer(r'"i"\s*:\s*(\d+)\s*,\s*"brief"\s*:\s*"([^"]*)"', text):
        idx = int(m.group(1)) - 1
        if 0 <= idx < n:
            out[idx] = m.group(2).strip()
    return out


def generate_briefs(cfg: dict, items: list[HotItem]) -> list[HotItem]:
    """为 items 生成中文提要，就地写入 item.brief。失败时静默降级。

    分批处理：一次请求最多 BATCH 条，避免单次输出过长导致 LLM 漏条目。
    """
    llm = cfg.get("llm", {})
    if not llm.get("enabled", False):
        return items
    api_key = llm.get("api_key", "")
    if not api_key:
        print("  ! LLM: 未配置 api_key，跳过提要生成", flush=True)
        return items
    if not items:
        return items

    base = (llm.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
    model = llm.get("model", "deepseek-chat")
    BATCH = 6
    ok_total = 0

    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(batch)},
            ],
            "temperature": llm.get("temperature", 0.3),
            "max_tokens": 1200,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            sess = requests.Session()
            sess.trust_env = False     # 避开本机代理
            r = sess.post(f"{base}/chat/completions", json=payload,
                          headers=headers, timeout=llm.get("timeout", 60))
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            briefs = _parse_response(content, len(batch))
            for it, b in zip(batch, briefs):
                if b and len(b) <= 80:
                    it.brief = b
                    ok_total += 1
        except Exception as e:
            print(f"  ✗ LLM 批次 {start // BATCH + 1} 失败: {str(e)[:110]}", flush=True)

    print(f"  ✓ LLM: {ok_total}/{len(items)} 条提要生成成功", flush=True)

    # 降级兜底：没有 brief 的用清洗后的原文摘要
    for it in items:
        if not it.brief:
            it.brief = it.summary[:60] if it.summary else it.title

    return items
