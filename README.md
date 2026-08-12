# GitHub Frontier Radar

一个长期运行的个人开源情报雷达：每日收集 GitHub 信号和 Star 快照，每周在严格预算内做语义初筛、生成中文简报，并投递飞书卡片与 Markdown 周报。正常推荐最多 10 个项目；若严格推荐只有 1–3 个，则补充质量、相关性和实用性均达标的“学习候选”至 4 个；若严格推荐为 0，则发送最多 3 个学习候选，并在报告中明确标注。

产品约束和分层设计见 [架构文档](docs/ARCHITECTURE.md)。V1 的 STEP 0–9 已完成：基础工程、采集、快照、打分、LLM 情报、选择器、报告/飞书、周编排、GitHub Actions 与最终验收。

## 本地运行

需要 Python 3.12+。以下两种方式任选其一：

```bash
# 使用 uv（推荐开发时使用锁文件）
uv sync --all-groups
uv run pytest

# 或使用标准 pip
python -m pip install . pytest
pytest
```

日采集无需 LLM 或飞书密钥：

```bash
export GITHUB_TOKEN="..."  # 可选，但建议设置以获得更高 GitHub API 限额
github-frontier-radar daily
```

每周任务使用 OpenAI Chat Completions 兼容接口。先在 `config/radar.yaml` 填写实际可用的 `llm.model`；如使用兼容服务，再填 `llm.api_base_url`。然后：

```bash
export LLM_API_KEY="..."
export FEISHU_WEBHOOK_URL="..."
github-frontier-radar weekly \
  --report-base-url "https://github.com/OWNER/REPO/blob/main/reports"
```

`--report-base-url` 可选；提供后飞书卡片会含本周 Markdown 周报按钮。所有密钥只能来自环境变量，绝不写入 YAML、命令行参数或提交记录。

## GitHub Actions 部署

已提供两个可手动触发的工作流：

- `daily_snapshot.yml`：每天 **06:23（Asia/Shanghai）** 采集并提交 `data/state.json`。
- `weekly_radar.yml`：每周一 **09:17（Asia/Shanghai）** 生成周报、发送飞书卡片，并提交 `reports/YYYY-Www.md` 与特征历史。

部署前按顺序完成：

1. 将项目初始化、提交并推送到 GitHub；`data/` 与 `reports/` 必须保留在仓库中，供工作流持久化状态和报告。
2. 在仓库 **Settings → Actions → General → Workflow permissions** 允许工作流读写仓库内容；工作流已显式采用最小的 `contents: write` 权限。
3. 在 **Settings → Secrets and variables → Actions** 新建：
   - `LLM_API_KEY`：周报模型 API 密钥。
   - `FEISHU_WEBHOOK_URL`：飞书自定义机器人 Webhook。
   - `GITHUB_TOKEN` 由 GitHub Actions 自动提供，通常无需创建 PAT。
4. 在 `config/radar.yaml` 设置 `llm.model`。确认 `external_search.enabled: false`；当前 CLI 未接入外部搜索供应商，误开启会主动失败，避免悄悄跳过该能力。
5. 先在 Actions 页面分别执行一次 **Run workflow**：先 daily，再 weekly。确认产生 `data/state.json` 与 `reports/` 文件，并收到飞书卡片。

工作流以同一个并发组串行执行，避免日任务和周任务同时覆写状态；没有数据变化时不会创建空提交。公开仓库长时间无活动时，GitHub 可能暂停计划任务，恢复后请手动触发一次并检查 Actions 页面。

日采集的日期以 `timezone`（默认 `Asia/Shanghai`）计算，避免 Ubuntu Runner 的 UTC 日期把 06:23 快照记到前一天。GitHub Search 请求也受 `max_daily_search_queries` 限制：默认每天最多 15 条查询、每条最多两页；高信号的 Breakout/Exploration 每天执行，其余 Query Bank 与 Watchlist 查询按日期轮换。

## 运行边界

- 仓库仅经 GitHub API/Trending HTML 读取；不会 clone、安装或执行第三方项目代码。
- 日任务只采集、去重、刷新和记录快照；不调用 LLM、不发送飞书。
- 周任务将 LLM 调用限制在配置预算内，JSON 输出经 Pydantic 校验，单批失败会降级而非中断全部分析。
- Markdown 写入采用原子替换；飞书卡片会按 UTF-8 字节数拆分并对限流、服务端错误做有界重试。
