# AI 热点日报

中文优先的 AI 每日简报。相比通用「热点榜单」，它解决三个问题：

1. **有摘要** —— 每条热点配一句中文提要，说清「这是什么 / 为什么值得看」
2. **分数可比** —— 热度只在同源内比较，不再拿 GitHub star 数硬碰 arXiv 关键词分
3. **分组呈现** —— 按「值得一读 / 新项目 / 论文速览」分块，而不是一锅乱炖的 Top 10

## 产出

| 文件 | 说明 |
|---|---|
| `docs/index.html` | GitHub Pages 页面，含简报预览 |
| `docs/brief.txt` | 简报纯文本（企业微信推送的实际内容） |
| `docs/data.json` | 结构化数据快照 |

## 数据源

| 源 | 内容 | 入选规则 |
|---|---|---|
| GitHub | AI 相关热门仓库 | 5 个精准 topic 查询 + 描述关键词校验 + star 门槛 + 7 天内活跃 |
| 媒体 RSS | TechCrunch AI / HN / Simon Willison / HuggingFace / OpenAI | 按发布时间排序 |
| arXiv | cs.AI / cs.CL | 2 天内 + 工程关键词命中 |

## 本地运行

```bash
pip install -r requirements.txt

python -m src.main --no-llm        # 不调 LLM，用原文摘要
python -m src.main --dry-run       # 生成但不推送
python -m src.main                 # 完整（需配 LLM_API_KEY）
```

### 环境变量

| 变量 | 用途 |
|---|---|
| `GH_PAT` | GitHub Token（提升 API 限额） |
| `LLM_API_KEY` | LLM 提要生成 |
| `LLM_BASE_URL` | 默认 `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 默认 `deepseek-chat` |
| `WECOM_WEBHOOK_KEY` | 企业微信机器人（需同时把 `config.yaml` 的 `wecom.enabled` 设为 `true`） |

## 推送开关

推送**默认关闭**。确认简报效果满意后：

1. 在仓库 Secrets 里加 `WECOM_WEBHOOK_KEY`
2. 把 `config.yaml` 的 `wecom.enabled` 改成 `true`
3. 提交后，每天 UTC 23:30（北京 07:30）自动生成并推送

## 设计说明

**为什么分数要归一化？**
原版把三种量纲的分数直接排序：GitHub 是 `star÷10 封顶 100`，媒体是 `30 + 关键词加分`，arXiv 是 `30 + 8×命中数`。结果 `⭐180` 和 `⭐55` 同列，读者会误判热度。现在改为按来源分组后组内 min-max 归一化，跨组用 `source_weight` 加权，分数语义才成立。

**为什么简报只展示 9 条？**
有了摘要后，9 条有效信息 > 30 条纯标题。分组配额在 `config.yaml` 的 `brief_per_source` 里可调。
