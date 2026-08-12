# GitHub Frontier Radar
## GitHub 前沿开源项目智能雷达
### Architecture & Development Specification v1.0

---

# 0. 文档目的

本文档是 GitHub Frontier Radar 的：

- 产品规则
- 推荐算法设计
- 系统架构
- 数据结构协议
- 模块边界
- 成本控制方案
- GitHub Actions 部署方案
- Codex 开发执行规范

后续代码生成模型应优先遵循本文档。

如果实现细节与本文档的产品原则冲突：

> **优先修改实现，不修改产品原则。**

---

# 1. 产品定义

GitHub Frontier Radar 不是：

- GitHub Star 排行榜
- GitHub Trending 搬运工具
- README 摘要机器人
- 源码学习工具
- 单纯根据个人兴趣推荐项目的算法

它是：

> **一个长期运行的个人开源世界情报雷达。**

系统每周回答：

1. 最近开源世界有什么真正值得知道的新东西？
2. 哪些项目正在明显升温？
3. 为什么它们最近突然受到关注？
4. 它们到底是干什么的？
5. 哪些人适合使用？
6. 哪些可能真正改善用户的工作方式？
7. 用户应该：
   - 🔥 现在试试
   - ⭐ 收藏以后用
   - 👀 知道即可

---

# 2. V1 的明确边界

## V1 要做

### Discovery

持续发现：

- 新项目
- 突然爆发的项目
- 老项目重大更新重新爆发
- GitHub Trending 项目
- AI / Coding / Automation / Data 等实用项目
- 用户关注领域之外的重要项目

### Intelligence

用大白话解释：

- 这是什么
- 能干什么
- 为什么现在火
- 对用户有什么价值
- 适合哪些人
- 使用成本
- 一个最重要的风险
- 是否值得现在尝试

### Delivery

每周通过飞书推送最多 10 个项目。

---

# 3. V1 明确不做

第一版禁止为了“功能丰富”加入：

- 源码架构分析
- Dependency 深度分析
- 自动 Clone 第三方仓库
- 执行第三方代码
- 自动安装项目
- Embedding / Vector Database
- LangChain
- Agent Framework
- PostgreSQL
- Redis
- FastAPI
- Kubernetes
- 微服务架构
- Web Dashboard
- 复杂推荐模型

第一版目标：

> **一个稳定、容易理解、容易测试、低成本运行的 Python 自动化程序。**

---

# 4. 一个重要的技术事实：7 日 Star 增长怎么得到

不能依赖：

```text
读取每一个 Stargazer 的 starred_at
→ 统计过去 7 天新增 Star
```

GitHub 已在 2026 年收紧 Stargazer listing endpoint 的访问，公开仓库的 Stargazer 列表不能再作为这个系统长期可靠的数据基础。citeturn562536search1

因此采用：

# Snapshot Strategy

每天记录：

```text
Repository
Date
Current Stars
```

例如：

```text
2026-08-01   1,250
2026-08-02   1,390
2026-08-03   1,620
...
2026-08-08   3,900
```

于是：

```text
Stars 7d =
Stars Today
-
Stars 7 Days Ago
```

这是 Radar 自己掌握的第一方趋势数据。

---

# 5. 冷启动问题

系统刚开始运行的前 7 天：

> 没有自己的完整历史。

因此使用三个 fallback：

### Signal A

GitHub Trending。

GitHub 当前的 weekly Trending 页面仍直接展示：

```text
xxxx stars this week
```

可以作为冷启动阶段的重要趋势信号。citeturn258259view0

### Signal B

项目年龄与当前 Star。

例如：

```text
创建 10 天
3,000 Stars
```

即使没有完整 Snapshot：

> 也明显属于快速增长项目。

### Signal C

最近 Release / 创建 / Push 等事件。

---

从第 8 天开始：

> Radar 自己的 Snapshot 数据逐渐成为主要趋势依据。

---

# 6. 为什么采用“每日采集 + 每周分析”

整个系统实际上拥有两个 Pipeline。

---

# DAILY PIPELINE

每天运行。

职责：

```text
发现项目
↓
刷新基础 Metadata
↓
保存 Star Snapshot
↓
结束
```

每日任务：

### 不调用 LLM

### 不调查外部社区

### 不发送飞书

因此成本非常低。

---

# WEEKLY PIPELINE

每周运行一次。

职责：

```text
读取最近历史
↓
计算趋势
↓
筛候选
↓
LLM 轻量判断
↓
必要时调查热度原因
↓
生成最终 Intelligence Brief
↓
筛 ≤10
↓
飞书
```

---

# 7. 总体数据流

```text
               GitHub Actions
                     │
          ┌──────────┴──────────┐
          │                     │
       DAILY                  WEEKLY
          │                     │
          ▼                     ▼
 GitHub Candidate          Load History
   Discovery                   │
          │                     ▼
          ▼                Calculate
 GitHub Trending            Momentum
          │                     │
          ▼                     ▼
 Repository Search        Hard Filter
          │                     │
          └──────► State ◄──────┘
                    │
                    ▼
           Deterministic Ranking
                    │
              ~30 candidates
                    │
                    ▼
              LLM Triage
                    │
              ~12 candidates
                    │
                    ▼
            Evidence Enrichment
                    │
              GitHub Evidence
                    │
             Optional Web Search
                    │
                    ▼
             Final LLM Brief
                    │
                    ▼
             Final Selection
                    │
                 ≤ 10
                    │
           ┌────────┴────────┐
           ▼                 ▼
       Feishu Card      Markdown Report
```

---

# 8. Candidate Discovery

发现阶段不是只运行一个 Search Query。

采用多个：

# Discovery Channels

---

## Channel A — GitHub Trending

采集：

```text
daily
weekly
```

信息：

```text
repo
description
language
total stars
weekly/daily stars
trending rank
```

GitHub Trending 属于：

> High-Signal Source

但必须作为可降级 Adapter。

因为 Trending 是网页而不是稳定 REST API contract。

如果 HTML Parser 某天失效：

```text
log warning
↓
skip Trending
↓
其他 Discovery Channel 继续运行
```

禁止整个 Pipeline 因 Trending Parser 失败而停止。

---

# 9. Channel B — Recent Breakout Search

寻找最近出现、已经开始获得关注的新项目。

例如构造：

```text
created:>=DATE
stars:>=MIN
archived:false
mirror:false
template:false
```

GitHub Repository Search 原生支持 Star、创建日期、最近 push、language、topic、archived、mirror、template 等搜索条件。citeturn266459view1

搜索：

```text
最近 30 天
最近 90 天
最近 180 天
```

目的是找到：

> 还没有进入 Trending，但已经开始快速积累 Star 的项目。

---

# 10. Channel C — Practical Query Bank

维护：

```text
config/queries.yaml
```

示例：

```yaml
ai_coding:
  - coding agent
  - ai coding
  - coding assistant
  - vibe coding
  - agentic coding

agent:
  - ai agent
  - agent framework
  - agent workflow
  - mcp

automation:
  - automation
  - workflow automation
  - desktop automation
  - task automation

browser:
  - browser automation
  - browser agent
  - web automation

data_collection:
  - web crawler
  - scraper
  - data extraction
  - web data

developer_tools:
  - developer tool
  - cli productivity
  - terminal productivity

finance:
  - market data
  - quantitative finance
  - backtesting
  - financial analysis
```

自动附加：

```text
archived:false
mirror:false
template:false
pushed:>=RECENT_DATE
```

Query Bank 是配置。

不是 Python 硬编码。

---

# 11. Channel D — Exploration

这是防止信息茧房的重要来源。

主动搜索：

```text
最近新建
+
Star 上升明显
+
不限制垂直领域
```

因此可能发现：

- Database
- Terminal
- Compiler
- Browser
- Security
- DevOps
- New Runtime
- 新型开发工具

这些项目即使与当前个人关注方向不相关：

> Global Significance 足够高仍可进入 Radar。

---

# 12. Channel E — High-Signal Owners

维护可配置：

```text
config/watchlist.yaml
```

例如：

```yaml
owners:
  - ...
```

这里可以放：

- 重要技术公司
- 重要 AI Lab
- 高影响力开源组织

检测：

```text
new repository
major new release
```

这解决：

> 一个重要项目今天刚开源，还没有来得及积累 7 日 Star。

不要把组织列表写死在评分代码中。

---

# 13. Candidate Pool

Daily Discovery 最终合并：

```text
Trending
+
Recent Breakout
+
Practical Search
+
Exploration
+
Owner Watch
```

使用：

```text
repo_id
```

去重。

目标不是无限采集。

建议：

```text
每日 Candidate Pool：
100～300

Tracked Repository 上限：
约 600
```

这些数字全部配置化。

---

# 14. State

V1 不需要数据库服务器。

使用：

```text
data/state.json
```

状态存放在 Radar 自己的 GitHub Repo 中。

---

# 15. State 示例

```json
{
  "version": 1,

  "repositories": {
    "123456": {
      "full_name": "owner/project",

      "first_seen": "2026-08-01",
      "last_seen": "2026-08-12",

      "last_featured": null,
      "feature_count": 0,

      "snapshots": [
        {
          "date": "2026-08-05",
          "stars": 1200,
          "forks": 90
        },
        {
          "date": "2026-08-12",
          "stars": 2600,
          "forks": 160
        }
      ]
    }
  }
}
```

---

# 16. State Retention

建议：

```text
Star Snapshot：保留 35 天

Featured History：
长期保留

Repository：
超过 30 天没有再次发现，可从活跃 Snapshot Pool 中清除
```

但：

```text
last_featured
feature_count
```

仍可保留为精简 History。

---

# 17. Radar 的核心评分不是一个 Score

这是架构中非常重要的设计。

禁止设计：

```text
score = 84
```

然后所有项目简单排序。

一个项目进入 Radar 存在两条完全不同的路线。

因此首先分别计算：

# Global Significance

和：

# Personal Utility

---

# 18. Global Significance

回答：

> **“这是不是本周开源世界真正值得知道的事情？”**

0～100。

建议组成：

```text
Momentum          60%
Event Importance  25%
External Buzz     15%
```

如果 External Buzz 没有数据：

> 自动重新归一化已有项。

不要因为缺失数据自动给 0 分。

---

# 19. Momentum Score

Momentum 不应该只看：

```text
+Stars
```

而应该同时考虑：

### Absolute Growth

例如：

```text
+5,000 stars
```

### Relative Growth

例如：

```text
500 → 1,500
```

增长 200%。

### Trending Signal

是否：

```text
GitHub Trending
```

以及：

```text
stars this week
```

### Acceleration

有足够 Daily Snapshot 时比较：

```text
最近 3 天速度
vs
之前 4 天速度
```

---

# 20. 推荐 Momentum Formula

不要使用固定：

```text
+1000 = 高
```

因为不同星期 GitHub 整体热度不同。

使用：

# Percentile Ranking

例如在这一周候选中：

```text
Absolute Growth percentile
Relative Growth percentile
Acceleration percentile
Trending percentile
```

建议：

```text
Momentum =

45% Absolute Growth Percentile
30% Relative Growth Percentile
15% Trending Signal
10% Acceleration
```

这样：

```text
800 stars
+500
```

和：

```text
30k stars
+2k
```

都有机会被识别为异常增长。

---

# 21. Event Importance

检测：

```text
repository created recently
major release recently
important organization newly open sourced
old project suddenly active again
```

Event 不是 LLM 随便猜。

必须由 Evidence 支持。

---

# 22. Personal Utility

回答：

> **“这个东西对用户有没有现实价值？”**

主要由轻量 LLM Semantic Triage 提供。

关注：

```text
AI Coding
Desktop Automation
Browser Automation
Data Collection
Developer Productivity
Technology Exploration
Finance / Quant
```

当前用户 Profile：

```text
AI Coding              5
Desktop Automation     5
Browser Automation     5
Data Collection        5
Technology Exploration 5
Reduce Complexity      4
Finance / Quant        3
```

但：

> Personal Utility 不是硬过滤器。

---

# 23. Quality Confidence

独立计算：

```text
QualityConfidence
```

而不是塞进 Personal Utility。

它回答：

> **“我们有多大把握认为这不是一个垃圾项目？”**

考虑：

- archived?
- mirror?
- template?
- README 是否存在
- repo 是否有实际内容
- 最近是否维护
- 是否有 License
- 项目是否明显只是 Demo
- 项目是否极新、尚未验证

---

# 24. Hard Filters

直接排除：

```text
archived
mirror
obvious fork duplicate
empty repository
obvious spam
malicious-looking collection
```

普通 Fork 默认排除。

---

# 25. Demo / Meme

LLM Triage 输出：

```text
project_nature:
  tool
  library
  framework
  platform
  demo
  tutorial
  list
  meme
  unknown
```

如果：

```text
demo / meme
+
Global Significance 不高
```

排除。

如果：

```text
demo
+
Global Significance 极高
```

仍允许：

```text
👀 知道即可
```

---

# 26. Exploration Value

独立分数：

```text
ExplorationValue
```

原则：

> 一个与用户原有领域无关的普通项目，不能因为“陌生”就加分。

正确方式：

```text
Exploration Value
=
Global Significance
×
Domain Novelty
```

也就是说：

> **重要而陌生，才叫 Exploration。**

---

# 27. Personal Preference

V1：

```text
Preference Weight 非常低
```

甚至可以第一版先设置：

```text
0
```

保留数据结构，但暂时不参与主要排名。

原因：

当前用户仍处于：

> 看世界阶段。

未来真实反馈积累以后再开启。

---

# 28. 两条入选路线

## Route A — Global

如果：

```text
Global Significance >= threshold_A
AND
Quality >= minimum_A
```

可以入围。

---

## Route B — Utility

如果：

```text
Personal Utility >= threshold_B
AND
Quality >= stronger_minimum_B
```

可以入围。

因为：

> 不热门的小项目需要更多质量证据。

---

# 29. 推荐初始 Threshold

所有参数放进配置。

建议初始：

```text
GLOBAL_ENTRY = 72
GLOBAL_MIN_QUALITY = 45

UTILITY_ENTRY = 75
UTILITY_MIN_QUALITY = 65
```

这些不是永远正确的数学真理。

需要运行 4～8 周以后根据实际周报调整。

---

# 30. Final Priority

避免简单平均：

```text
50% Global
+
50% Utility
```

因为这样会杀死：

> 非常重要但与你暂时没关系

或者：

> 极有用但还没爆火

的项目。

推荐使用 OR-style Score：

```text
dominant = max(Global, Utility)
secondary = min(Global, Utility)

Priority =
dominant
+
0.20 × secondary
+
small Exploration bonus
```

最后归一化到：

```text
0～100
```

因此：

### Global 95 / Utility 30

依然很高。

### Global 35 / Utility 92

依然很高。

### Global 90 / Utility 90

最高。

---

# 31. Diversity

取消固定坑位。

禁止：

```text
3 个热点
3 个相关
2 个探索
```

这种配额。

但需要防止：

```text
10 个项目
8 个 Browser Agent
```

因此使用：

# Similarity Cap

默认：

```text
同一 Topic Cluster 最多 2 个
```

例如：

```text
browser_agent
coding_agent
database
crawler
terminal
```

如果第三个项目：

> 技术路线或价值明显不同

允许 Selector 例外。

---

# 32. LLM 成本控制

这是系统设计重点。

禁止：

```text
发现 300 个项目
↓
300 个全部读 README
↓
300 次 LLM
```

正确流程：

```text
200～300 discovered
        ↓
deterministic filter
        ↓
40
        ↓
deterministic ranking
        ↓
20～30
        ↓
cheap LLM triage
        ↓
12～15
        ↓
evidence enrichment
        ↓
最多 10 个 final brief
```

---

# 33. LLM Stage 1 — Semantic Triage

只针对：

```text
约 20～30 个项目
```

输入保持很短：

```text
name
description
topics
language
stars
growth metrics
created_at
pushed_at
README 前 3000～4000 chars
```

不读取：

```text
Source Tree
Dependencies
Architecture
```

输出：

```json
{
  "project_nature": "tool",
  "category": "browser_automation",

  "plain_summary": "...",

  "personal_utility": 82,
  "practical_value": 88,

  "target_users": [
    "AI developers",
    "automation users"
  ],

  "adoption_friction": 55,

  "demo_probability": 0.05,

  "confidence": 0.86
}
```

---

# 34. Stage 1 可以 Batch

为了减少 Prompt Overhead：

```text
一次分析 3～5 个 Repository
```

输出：

```json
{
  "repositories": [...]
}
```

必须保证：

```text
repo_id
```

严格对应。

如果 Batch 中一个项目解析失败：

> 不允许整批数据消失。

---

# 35. LLM Stage 2 — Final Intelligence Brief

只对：

```text
最终约 6～10 个项目
```

生成周报内容。

输入：

```text
metadata
trend metrics
README relevant excerpt
latest release evidence
external evidence if available
user profile
```

---

# 36. README Token 控制

不直接把巨大 README 全塞进去。

Triage：

```text
MAX_README_CHARS = 4,000
```

Final：

```text
MAX_README_CHARS = 12,000
```

优先提取：

```text
Introduction
Features
Use Cases
Installation
Quick Start
Pricing / hosted service
```

不用解析：

```text
Architecture
Contributor Guide
full API reference
```

---

# 37. Why Hot Evidence Engine

“为什么火”不能完全交给 LLM。

先建立 Evidence。

---

# 38. Evidence Level 1 — GitHub

调查：

```text
created_at
latest release
release notes
recent push
README announcement
Trending appearance
Star acceleration
```

生成：

```json
{
  "source": "github_release",
  "fact": "v2.0 released 4 days ago",
  "published_at": "...",
  "url": "...",
  "confidence": "high"
}
```

---

# 39. Evidence Level 2 — External Search

只有：

```text
明显爆发
+
GitHub 内部解释不足
```

才调用外部搜索。

架构使用：

```text
ExternalSearchProvider
```

接口。

不要在核心业务代码绑定：

```text
某一个搜索厂商
```

---

V1 可以：

```text
WEB_SEARCH_ENABLED=false
```

正常运行。

以后只需要增加 Provider：

```text
Provider A
Provider B
Provider C
```

无需修改 Ranker。

---

# 40. External Search 的职责

只调查：

> 为什么最近受到关注。

不负责无限制寻找 GitHub 项目。

原因：

Candidate Discovery 的目标对象本身就是 GitHub Repository。

GitHub 数据更结构化。

外部 Web：

> 主要用来解释传播原因和社区讨论。

---

# 41. Evidence Confidence

Evidence：

```text
FACT
LIKELY
UNKNOWN
```

Final Brief 不允许把：

```text
LIKELY
```

写成确定事实。

---

# 42. Final LLM Prompt 原则

LLM 必须被明确要求：

```text
Do not invent reasons for popularity.

Use only supplied evidence.

Distinguish facts from inference.

If the evidence is insufficient,
say that the cause cannot be confirmed.

Do not discuss source-code architecture.

Use plain Chinese.

Avoid marketing language.

Explain the project as if speaking
to a technically curious non-expert.
```

---

# 43. Final Brief Schema

```json
{
  "repo_id": 123,

  "one_liner": "",

  "what_it_does": "",

  "why_hot": {
    "text": "",
    "confidence": "fact"
  },

  "why_it_matters_to_user": "",

  "target_users": [],

  "cost": {
    "type": "free",
    "note": ""
  },

  "adoption_friction": {
    "score": 3,
    "summary": ""
  },

  "main_risk": "",

  "recommendation": "try",

  "recommendation_reason": ""
}
```

Recommendation enum：

```text
try
save
know
```

---

# 44. LLM 不拥有最终排行榜控制权

LLM 可以给：

```text
Personal Utility
Practical Value
Category
Nature
Friction
```

但是：

> 最终 Selector 仍是 deterministic Python。

禁止：

```text
“LLM，请选出今天最好的十个项目。”
```

这样不可复现，也难测试。

---

# 45. Final Recommendation 规则

### 🔥 TRY

满足大致：

```text
Personal Utility 高
Quality 足够
没有严重风险
虽然有门槛但现实可用
```

---

### ⭐ SAVE

例如：

```text
价值很高
但当前使用场景不强

或者

非常新
值得继续观察

或者

门槛较高
目前没必要马上安装
```

---

### 👀 KNOW

例如：

```text
Global Significance 很高
但 Personal Utility 较低
```

典型：

> 一个与你当前领域无关、但非常重要的新数据库。

---

# 46. Repeat Recommendation

Featured History 保存：

```text
last_featured
feature_count
```

默认：

```text
COOLDOWN_DAYS = 56
```

冷却期内通常不重复。

---

允许重新进入：

```text
Major Release
Exceptional New Momentum
Major Event
```

显示：

```text
↩ 曾在 8 周前出现，本周因为 XXX 再次进入 Radar。
```

---

# 47. 飞书 V1

使用：

> Custom Bot Webhook

足够。

飞书官方说明，自定义机器人适合向指定群聊周期性推送静态内容；消息卡片可以通过 Webhook 发送。citeturn562536search3turn562536search5

---

# 48. 一个重要限制

Custom Bot Card：

> 可以 open_url。

但是：

> 不支持把 Button Action 回传到开发者服务端。

飞书官方将这种卡片定义为静态内容，只支持链接跳转。citeturn562536search6

所以 V1：

```text
[打开 GitHub]
[查看完整周报]
```

可以。

但：

```text
[👍]
[👎]
[已使用]
```

暂时不做真正交互。

---

# 49. Feedback V2

未来如果反馈功能真的值得：

升级：

```text
Feishu Custom Bot
→
Feishu App Bot
```

再增加：

```text
callback
feedback store
personal preference
```

V1 不提前承担这个复杂度。

---

# 50. 飞书 Card 结构

不要一项目一卡。

也不要：

```text
10 个项目塞成一个 20KB 巨卡
```

飞书自定义机器人目前单次请求体上限为 20 KB。citeturn562536search5

因此 Card Builder 必须：

```text
计算 JSON bytes
```

接近：

```text
16～18 KB
```

主动分卡。

---

# 51. 周报建议布局

第一张：

```text
GitHub Frontier Radar
2026 Wxx

本周共发现 X 个值得关注项目

本周观察：
一句话概括本周明显趋势。
```

然后：

```text
Project 1
Project 2
Project 3
...
```

必要时：

```text
Radar 1/2
Radar 2/2
```

---

# 52. 单项目卡片协议

```text
🔥 owner/project

一句话
XXXX

热度
本周 +2.4K ⭐

为什么火
XXXX

它能做什么
XXXX

对你的价值
XXXX

适合
AI 开发者 / 自动化用户

成本
开源免费；需要模型 API

⚠️
项目只有三周，目前稳定性仍需观察

结论
🔥 建议试试

热度       ★★★★★
与你相关   ★★★★☆
实际价值   ★★★★☆
上手难度   ★★★☆☆

[打开 GitHub]
[完整周报]
```

---

# 53. Markdown Report

每周同时生成：

```text
reports/2026-W33.md
```

内容可以比飞书略详细。

用途：

### 1

长期记录。

### 2

飞书：

```text
查看完整周报
```

可以链接到 GitHub Report。

### 3

半年以后形成自己的：

> Open Source Intelligence Archive。

---

# 54. Python 技术栈

保持极简。

---

## Python

建议：

```text
Python 3.12+
```

---

## HTTP

```text
httpx
```

负责：

- GitHub REST
- GitHub Trending
- LLM API
- Feishu
- Optional Web Search

---

## HTML

```text
beautifulsoup4
```

仅用于：

```text
GitHub Trending
```

不要泄漏到其他业务模块。

---

## Schema

```text
pydantic
```

所有跨模块数据必须有 Model。

禁止：

```python
dict[str, Any]
```

满项目乱飞。

---

## Config

```text
pydantic-settings
PyYAML
```

---

## Retry

```text
tenacity
```

---

## Test

```text
pytest
```

---

# 55. 明确不使用

V1 不安装：

```text
langchain
llama-index
sqlalchemy
fastapi
flask
celery
redis
pandas
numpy
chromadb
```

除非后续出现真正需求。

---

# 56. GitHub API Version

所有 REST API 请求显式发送：

```text
X-GitHub-Api-Version: 2026-03-10
```

截至目前 GitHub 官方列出的受支持 REST API 版本包括 `2026-03-10` 和 `2022-11-28`，显式指定版本可以避免未来默认版本变化造成不可预期行为。citeturn581421search0

---

# 57. GitHub Rate Limit Design

GitHub Search 有独立且更严格的 Rate Limit，因此 Query Bank 不应该粗暴并发几十个请求。citeturn810105search2turn810105search8

要求：

```text
low concurrency
rate-limit header tracking
retry-after
exponential backoff
query rotation
```

读取：

```text
x-ratelimit-remaining
x-ratelimit-reset
```

GitHub 官方建议从响应 Header 判断额度。citeturn810105search4

---

# 58. 推荐项目目录

```text
github-frontier-radar/
│
├── src/
│   └── radar/
│
│       ├── models.py
│       ├── config.py
│
│       ├── github_sources.py
│       ├── state_store.py
│       ├── scoring.py
│       ├── intelligence.py
│       ├── selector.py
│       ├── feishu.py
│       ├── report.py
│
│       ├── daily.py
│       └── weekly.py
│
├── config/
│   ├── radar.yaml
│   ├── queries.yaml
│   ├── user_profile.yaml
│   └── watchlist.yaml
│
├── data/
│   └── state.json
│
├── reports/
│
├── tests/
│
├── .github/
│   └── workflows/
│       ├── daily_snapshot.yml
│       └── weekly_radar.yml
│
├── pyproject.toml
├── README.md
└── .gitignore
```

这已经足够模块化。

不要继续拆 30 个 Python 文件。

---

# 59. models.py

定义：

```text
RepoCandidate
RepoSnapshot
GrowthMetrics
TriageResult
EvidenceItem
IntelligenceBrief
ScoreBreakdown
RankedCandidate
RadarReport
DeliveryResult
RadarState
```

---

# 60. github_sources.py

负责：

```text
GitHub Search
Trending Parser
Repo Metadata
README
Latest Release
```

禁止：

```text
scoring
LLM
state write
Feishu
```

---

# 61. state_store.py

负责：

```text
load
save
snapshot
history
cooldown
prune
```

写文件必须：

```text
atomic
```

即：

```text
write temp
fsync if appropriate
replace
```

---

# 62. scoring.py

纯函数。

负责：

```text
growth
momentum
event
quality
global significance
```

禁止网络访问。

---

# 63. intelligence.py

负责：

```text
LLM Triage
Evidence synthesis
Final Brief
```

同时定义 Provider abstraction：

```python
class LLMProvider(Protocol):
    ...
```

业务逻辑不得与某一家模型 API 强绑定。

---

# 64. selector.py

负责：

```text
Route A
Route B
Priority
Cooldown
Similarity
Diversity
Final ≤10
```

纯业务逻辑。

---

# 65. feishu.py

负责：

```text
build card
byte-size guard
split
webhook
retry
```

---

# 66. report.py

负责：

```text
RadarReport
→
Markdown
```

---

# 67. daily.py

Orchestrator。

流程：

```text
discover
dedupe
refresh
snapshot
prune
save
```

业务逻辑不要写在这里。

---

# 68. weekly.py

Orchestrator。

流程：

```text
load
discover
growth
pre-score
triage
rank
evidence
brief
select
report
notify
mark_featured
save
```

---

# 69. Secrets

不创建自己的普通 GitHub PAT 作为默认方案。

GitHub Actions 每个 Job 自动获得 `GITHUB_TOKEN`；GitHub 官方建议使用最小必要权限。citeturn810105search5turn810105search1

Secrets：

```text
LLM_API_KEY
FEISHU_WEBHOOK_URL
```

Optional：

```text
FEISHU_SIGNING_SECRET
WEB_SEARCH_API_KEY
```

禁止 Secrets：

```text
写入 config
写入 log
写入 report
写入异常 traceback
```

---

# 70. Workflow Permission

Daily / Weekly 如果需要把：

```text
state.json
reports/*.md
```

commit 回仓库：

```yaml
permissions:
  contents: write
```

否则：

```text
contents: read
```

遵守 Least Privilege。

---

# 71. Schedule

GitHub Actions 当前支持：

```text
POSIX cron
+
IANA timezone
```

并且官方明确提醒整点属于高负载时间，Scheduled Workflow 可能延迟，因此避免 `xx:00`。citeturn810105search3

建议默认：

```text
timezone:
Asia/Shanghai
```

Daily：

```text
06:23
```

Weekly：

```text
Monday 09:17
```

全部配置可改。

---

# 72. GitHub Actions 的一个运行风险

GitHub 官方说明：

> 公共 Repository 如果 60 天没有仓库活动，Scheduled Workflow 可能被自动禁用。citeturn810105search0

我们的 Radar：

> 每日 Snapshot 本身会产生 State Commit，

所以正常情况下会持续有活动。

但 README 必须记录这个限制。

---

# 73. Error Philosophy

系统不能：

> 一个 Repo 出错 → 整个周报失败。

采用：

# Partial Failure

例如：

### README 404

```text
skip README
continue
```

### Trending Parser Failure

```text
skip Trending
continue
```

### External Web Search Failure

```text
why_hot confidence lower
continue
```

### One LLM Analysis Failure

```text
drop candidate
or degraded brief
continue
```

### Feishu Failure

这是：

```text
pipeline failure
```

因为最终 Delivery 没有完成。

---

# 74. Logging

输出：

```text
discovered: 247
after hard filter: 138
pre-ranked: 40
LLM triage: 24
shortlist: 12
final: 8
feishu cards: 2
```

以及：

```text
github requests
llm calls
external search calls
```

绝不打印 Key。

---

# 75. Cost Guardrails

config：

```yaml
limits:

  max_daily_candidates: 300
  max_tracked_repos: 600

  max_weekly_prescore: 40

  max_llm_triage: 25
  llm_triage_batch_size: 5

  max_external_research: 8

  max_final_briefs: 10

  max_readme_chars_triage: 4000
  max_readme_chars_final: 12000

  final_report_max_projects: 10
```

任何代码都不得绕过 Guardrail。

---

# 76. Quality Guardrail

最终：

```text
0 projects
```

也是合法结果。

如果没有项目达到要求：

飞书发送：

```text
本周 Radar 没有发现达到推荐阈值的项目。
宁缺毋滥。
```

不要降低阈值强行凑数。

---

# 77. Testing Strategy

至少覆盖：

### State

```text
first run
7d delta
partial history
duplicate snapshot
prune
cooldown
atomic save
corrupt JSON
```

### GitHub

```text
search
rate limit
404
Trending
Trending HTML broken
README missing
release missing
```

### Scoring

```text
small fast-growing repo
large slow repo
old sudden revival
new repo
missing historical delta
percentile
```

### Selector

```text
Route A
Route B
quality gate
cooldown
same-category collision
fewer than 10
deterministic output
```

### LLM

```text
valid JSON
invalid JSON
missing field
hallucinated evidence
unknown why-hot
```

### Feishu

```text
payload < limit
auto split
webhook success
timeout
error
```

---

# 78. Definition of Done

第一版只有同时满足以下条件才算完成：

```text
pytest passes
daily pipeline works
weekly pipeline works

no secret in repository

first 7 days cold-start supported

snapshot correctly calculates growth

LLM only receives shortlisted repositories

≤10 final projects

no forced filler

Feishu payload size protected

failed Trending parser does not crash Radar

failed external search does not crash Radar

weekly Markdown report generated

state persists between GitHub Actions runs
```

---

# 79. 开发方式

对于 Codex：

禁止一次 Prompt：

> “帮我把这个系统全部做完。”

采用阶段式实现。

顺序：

```text
STEP 0
Project Foundation

STEP 1
GitHub Sources

STEP 2
State + Daily Snapshot

STEP 3
Scoring Engine

STEP 4
LLM Intelligence

STEP 5
Selector

STEP 6
Report + Feishu

STEP 7
Weekly Pipeline

STEP 8
GitHub Actions

STEP 9
Acceptance Test
```

每一步：

```text
Implement
↓
Test
↓
Fix
↓
Commit-ready
↓
再进入下一步
```

---

# 80. CODEX MASTER PROMPT

将本文档放进项目：

```text
docs/ARCHITECTURE.md
```

然后第一次给 Codex：

```text
You are the implementation engineer for a Python project named
GitHub Frontier Radar.

Read docs/ARCHITECTURE.md completely before modifying any files.

ARCHITECTURE.md is the product and engineering source of truth.

Your job is NOT to implement the whole project at once.

We will implement it incrementally.

Global engineering rules:

1. Python must use type hints.
2. Cross-module data must use Pydantic models.
3. Business logic should be deterministic and independently testable.
4. Network access must be isolated behind adapters.
5. Tests must never perform real GitHub, LLM, web-search, or Feishu requests.
6. Never hard-code or log secrets.
7. Do not execute or clone third-party GitHub projects.
8. Do not add frameworks or dependencies that are not justified by ARCHITECTURE.md.
9. Prefer simple code over abstraction for abstraction's sake.
10. Do not silently modify product requirements.
11. If an external API response is missing data, represent it explicitly instead of inventing values.
12. A failure analyzing one repository must not normally terminate the entire weekly pipeline.
13. All ranking behavior must be deterministic for identical inputs.
14. Do not implement future V2 features unless requested.
15. Run the relevant tests after every implementation step and fix failures before finishing.

Before editing:
- inspect the current repository
- read ARCHITECTURE.md
- explain briefly what you are going to change

After editing:
- run tests
- report what changed
- report test results
- report any assumption or unresolved issue
- stop

Do not proceed to the next development step until I explicitly ask.
```

---

# 81. CODEX STEP 0 — Foundation

```text
Implement STEP 0 of GitHub Frontier Radar.

Read docs/ARCHITECTURE.md first.

Only build the project foundation.

Create:

src/radar/__init__.py
src/radar/models.py
src/radar/config.py

config/radar.yaml
config/queries.yaml
config/user_profile.yaml
config/watchlist.yaml

tests/test_models.py
tests/test_config.py

pyproject.toml

Create strongly typed Pydantic models for:

RepoCandidate
RepoSnapshot
GrowthMetrics
TriageResult
EvidenceItem
IntelligenceBrief
ScoreBreakdown
RankedCandidate
RadarReport
DeliveryResult
RadarState

Important:

- Scores must have explicit valid ranges.
- Dates should use date/datetime types, not arbitrary strings internally.
- Repo identity should rely primarily on GitHub repo_id.
- Unknown values should be represented as None where appropriate.
- Do not use dict[str, Any] as a substitute for proper domain models.
- Configuration must support the guardrails defined in ARCHITECTURE.md.
- Secrets must come from environment variables and must not appear in YAML.
- user_profile.yaml should represent interest weights as configuration, not Python constants.
- queries.yaml and watchlist.yaml should be user-editable.

Do NOT implement:

GitHub networking
state persistence
scoring
LLM
Feishu
workflows

Tests must validate:
- invalid score ranges
- required IDs
- optional fields
- config loading
- missing required secret handling where applicable

Run pytest.

Stop after STEP 0.
```

---

# 82. CODEX STEP 1 — GitHub Sources

```text
Implement STEP 1: GitHub data sources.

Read ARCHITECTURE.md and the existing models.

Create:

src/radar/github_sources.py

tests/test_github_sources.py

Implement a GitHubSources abstraction providing:

search_repositories(...)
fetch_trending(period)
get_repository(...)
get_readme(...)
get_latest_release(...)

Use httpx.

Requirements:

- GitHub REST requests must send:
  Accept: application/vnd.github+json
  X-GitHub-Api-Version: 2026-03-10

- Support authentication through the configured GitHub token.
- Never log token values.
- Explicit connect/read timeout.
- Retry transient 429 and 5xx errors.
- Respect Retry-After where available.
- Read GitHub rate-limit response headers.
- Avoid aggressive concurrency.

Trending:

- Parse GitHub Trending daily and weekly pages.
- Extract repository name and available trend metrics.
- Keep HTML parsing in isolated helpers.
- A broken Trending HTML page must produce a warning and an empty/degraded result instead of crashing the application.

Search:

- accept query text
- support pagination with a configured maximum
- return RepoCandidate models

README:

- missing README is a normal condition

Latest release:

- missing release is a normal condition

Security:

Never:
- clone repositories
- execute repository code
- follow executable installation steps from README

Tests must mock all HTTP.

Cover:
- search success
- authentication header
- API version header
- pagination
- rate limit
- 404
- timeout
- 5xx retry
- README missing
- release missing
- Trending parsing
- broken Trending HTML

Run tests and stop.
```

---

# 83. CODEX STEP 2 — State + Daily Snapshot

```text
Implement STEP 2.

Create:

src/radar/state_store.py
src/radar/daily.py

tests/test_state_store.py
tests/test_daily.py

State must use:

data/state.json

Implement:

load_state
save_state
record_snapshot
get_historical_snapshot
calculate_star_delta
mark_seen
mark_featured
is_in_cooldown
prune_state

Rules:

- missing state file = empty state
- corrupt JSON = explicit StateCorruptionError
- same repo/date snapshot must not duplicate
- star delta must return None when no suitable historical point exists
- support partial-history metadata
- preserve featured history
- writes must be atomic
- keep configured snapshot retention
- respect max tracked repo guardrails

Implement daily pipeline:

discover candidates from configured sources
deduplicate by repo_id
refresh required metadata
record snapshots
prune
save

Daily pipeline must NOT:

call LLM
call external web search
send Feishu messages

All network components must be injectable/mocked.

Create deterministic tests including first-run and repeated-run behavior.

Run tests and stop.
```

---

# 84. CODEX STEP 3 — Scoring

```text
Implement STEP 3.

Create:

src/radar/scoring.py

tests/test_scoring.py

This module must contain pure deterministic business logic.

No:
HTTP
filesystem
environment access
LLM
Feishu

Implement:

calculate_growth_metrics
calculate_momentum
calculate_event_importance
calculate_quality_confidence
calculate_global_significance

Momentum should consider:

absolute 7-day growth
relative growth
Trending signal
acceleration when available

Prefer percentile-based scoring over scattered hard-coded absolute thresholds.

Missing metrics:

Do not convert unavailable data to zero blindly.
Reweight available components when appropriate.

Create a clearly defined ScoringConfig.

Test:

small project with explosive growth
large project with strong absolute growth
huge old project with weak recent growth
brand-new project
old project suddenly reviving
missing 7-day history
Trending fallback
deterministic percentile ranking

Every resulting score must expose its component breakdown.

Run tests and stop.
```

---

# 85. CODEX STEP 4 — LLM Intelligence

```text
Implement STEP 4.

Create:

src/radar/intelligence.py

tests/test_intelligence.py

Create an LLMProvider protocol.

Do not tightly couple the business layer to a specific model vendor.

Implement two operations:

1. semantic_triage
2. generate_final_brief

SEMANTIC TRIAGE

Input:

repo metadata
trend metrics
description
topics
short README excerpt
user profile

Output TriageResult.

It should classify:

project nature
topic/category
plain-language summary
personal utility
practical value
target users
adoption friction
demo probability
confidence

Do NOT ask for:

architecture
dependency analysis
source-code learning value

FINAL BRIEF

Input:

repo
scores
triage
README excerpt
EvidenceItems
user profile

Output IntelligenceBrief.

Rules for the LLM prompt:

- Use plain Chinese.
- Avoid marketing language.
- Do not invent facts.
- Do not invent popularity causes.
- Explain only from supplied evidence.
- Clearly distinguish fact, likely inference, and unknown.
- If evidence is insufficient, say so.
- Do not analyze source-code architecture.
- Do not output installation tutorials.
- Do not claim organizations use a project without evidence.
- Separate "suitable for" from "known adopters".

Validate every response through Pydantic.

Invalid structured output:
retry within configured limit.

Permanent failure:
raise or return a typed AnalysisUnavailable result that allows the weekly pipeline to continue.

Support batching for triage while retaining repo_id identity.

Respect:

max_llm_triage
batch size
README character limits
max_final_briefs

Mock all LLM calls in tests.

Run tests and stop.
```

---

# 86. CODEX STEP 5 — Selector

```text
Implement STEP 5.

Create:

src/radar/selector.py

tests/test_selector.py

Pure deterministic module.

Implement:

Route A:
Global Significance route

Route B:
Personal Utility route

Use configurable entry and quality thresholds.

Implement OR-style priority so that:

high Global + low Utility can still qualify

and:

low Global + high Utility can still qualify

while:

high Global + high Utility receives a synergy advantage.

Implement:

cooldown
repeat exception hooks
Exploration bonus
topic clustering
same-topic similarity cap
max final projects

Do not implement fixed category quotas.

Default max final projects:
10

It is valid to return fewer than 10.

It is valid to return zero.

Tests:

global-only important project
utility-only excellent small project
both high
both weak
low-quality utility project
cooldown
repeat exception
four similar Browser Agent projects
cross-domain important project
less than 10 qualifying projects
zero qualifying projects
deterministic ordering

Run tests and stop.
```

---

# 87. CODEX STEP 6 — Reports + Feishu

```text
Implement STEP 6.

Create:

src/radar/report.py
src/radar/feishu.py

tests/test_report.py
tests/test_feishu.py

report.py:

Convert RadarReport into:

reports/YYYY-Www.md

The Markdown report should preserve:

project
trend
plain-language explanation
why hot
utility
target users
cost
main risk
recommendation
score summary
GitHub link

feishu.py:

Build Feishu schema 2.0 interactive cards for a custom bot webhook.

Only use URL-open interactions.

Do not implement callback-based feedback buttons.

Each repository should show concise information:

name
one-liner
recent growth
why hot
what it does
why it matters
suitable users
cost
one main risk
recommendation
four visual scores

Buttons:

Open GitHub
Full Report

Implement UTF-8 byte-size checking on the complete webhook payload.

Do not approach the 20 KB hard limit.

Use a configurable safer threshold and split the report into multiple cards when necessary.

HTTP:

httpx
timeout
retry transient failures
typed permanent delivery error

Never log the full webhook URL.

Tests:

short report
10-project report
long Chinese content
automatic splitting
button URLs
unknown star growth
webhook success
timeout
API error

Run tests and stop.
```

---

# 88. CODEX STEP 7 — Weekly Pipeline

```text
Implement STEP 7.

Create:

src/radar/weekly.py

tests/test_weekly.py

Weekly orchestration:

1. load state
2. perform current discovery refresh
3. calculate growth
4. hard filter
5. deterministic pre-ranking
6. retain at most configured pre-score pool
7. run semantic LLM triage only within configured budget
8. calculate Personal Utility
9. calculate preliminary Route A / Route B scores
10. retain shortlist
11. gather GitHub evidence
12. call optional ExternalSearchProvider only when configured and justified
13. generate final briefs only within configured maximum
14. final deterministic selection
15. generate Markdown report
16. deliver Feishu
17. mark successfully featured repositories
18. save state

Important transactional rule:

Do not mark a project as successfully featured before successful report/delivery handling.

Partial repository failures must not normally stop the complete pipeline.

Enforce every cost guardrail.

Log pipeline counts:

discovered
hard-filtered
pre-ranked
LLM triaged
external researched
final briefed
selected
cards sent

Tests must mock all external services.

Include:

full happy path
one GitHub candidate failure
one LLM failure
external search unavailable
fewer than 10 selections
Feishu failure
state update correctness

Run tests and stop.
```

---

# 89. CODEX STEP 8 — GitHub Actions

```text
Implement STEP 8.

Create:

.github/workflows/daily_snapshot.yml
.github/workflows/weekly_radar.yml

Use the scheduling and security principles from ARCHITECTURE.md.

Requirements:

Python installation
dependency installation
pytest smoke gate where appropriate
daily command
weekly command

Use GitHub's automatically provided GITHUB_TOKEN.

Grant the minimum repository permissions required.

Secrets:

LLM_API_KEY
FEISHU_WEBHOOK_URL

optional:

FEISHU_SIGNING_SECRET
WEB_SEARCH_API_KEY

Default timezone:

Asia/Shanghai

Avoid scheduling at minute 00.

Provide workflow_dispatch in addition to schedule so the workflows can be tested manually.

Persist:

data/state.json

and weekly:

reports/*.md

using a safe git commit/push step.

Avoid creating a commit when nothing changed.

Never print secrets.

Update README with setup instructions.

Do not add unnecessary third-party GitHub Actions when a simple shell/Python step is sufficient.

Validate workflow YAML and run the Python test suite.

Stop after completing STEP 8.
```

---

# 90. CODEX STEP 9 — Final Acceptance Review

```text
Perform STEP 9: production-readiness review.

Do not add new product features.

Read ARCHITECTURE.md.

Review the entire repository against its Definition of Done.

Run the complete test suite.

Inspect:

secret handling
network timeouts
retries
rate-limit behavior
state atomicity
cold-start behavior
7-day star calculation
LLM guardrails
external-search degradation
selector determinism
cooldown
similarity handling
Feishu 20KB protection
GitHub Actions permissions
scheduled workflow configuration

Look specifically for accidental architectural violations such as:

LLM being called before deterministic filtering
unbounded candidate lists
README size not capped
hard-coded API keys
business logic inside workflow YAML
network requests inside scoring
project count being forced to 10
unknown data silently becoming zero
LLM inventing popularity reasons
third-party repository code being executed

Fix defects that violate the existing specification.

Do not redesign the product.

At the end provide:

1. test results
2. architecture compliance findings
3. remaining operational risks
4. exact manual setup steps I need to perform
5. first-run checklist

Stop.
```

---

# 91. 最终实现原则

整个项目应该始终保持：

```text
GitHub 负责提供信号

Python 负责筛选和计算

LLM 负责理解和解释

Evidence 负责约束 LLM

Selector 负责最终决定

Feishu 负责把结果送到你面前
```

而不是：

```text
把所有 GitHub 项目扔给 LLM
↓
让 AI 自己决定什么值得推荐
```

---

# 92. V1 的一句话架构

> **用每天的低成本数据采集建立自己的 GitHub 趋势时间序列，再用确定性算法从噪声中压缩候选，只把真正有机会进入周报的少量项目交给 LLM 做“大白话情报分析”，最终由可解释的规则选出最多 10 个项目，通过飞书送达。**

