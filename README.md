# sql-agent-kit

<p align="center">
  <b>生产级 Text-to-SQL Agent 工具包</b><br>
  自然语言 → SQL → 图表 → 分析结论，全链路多 Agent 智能协作
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/node-18+-green.svg" alt="Node.js 18+">
  <img src="https://img.shields.io/badge/license-MIT-brightgreen.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/framework-FastAPI-009688.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/frontend-Vue3-4FC08D.svg" alt="Vue 3">
</p>

https://github.com/user-attachments/assets/53503102-c8ab-4dd0-aa3b-38383ea60be2

![Web UI 截图](docs/images/screenshot004.png)

---

## 是什么

输入 **"上个月各商品的销售趋势如何？"**，多 Agent 流水线自动完成：

```
意图拆解 → SQL 生成 → 安全校验 → 执行 → 图表渲染 → 结论分析 → 质量评估
```

全程透明，每一步的思考过程都可以在 Web UI 中实时查看。

---

## 核心能力

### Agent 流水线

| Agent | 职责 |
|---|---|
| **Planner** | 拆解用户意图，判断图表类型，分解子问题 |
| **SQL Agent** | ReAct 链路生成并执行 SQL，失败自动重试自愈 |
| **Chart Agent** | 规则推断 + Planner 建议双层选型，自动生成 Plotly 交互图表 |
| **Summary Agent** | 基于数据输出 2–4 句中文分析结论 |
| **LLM-as-Judge** | 三维度（SQL 准确性 / 图表适配 / 结论质量）0–10 分评估 |

### 安全与可靠性

- **表名白名单** — 只允许查询指定的表，防止越权
- **语义注释层** — 模糊字段名加业务含义，显著提升 SQL 准确率
- **SQL 安全校验** — 强制 SELECT only，过滤所有写操作
- **错误自愈重试** — 执行失败自动反馈给 LLM 重试，最多 N 次
- **置信度评估** — 低置信度时提示用户确认，不静默执行
- **Few-shot 管理** — 持续积累正确示例，渐进提升准确率
- **查询日志** — 完整记录每次查询，支持搜索与回溯

---

## 快速开始

**环境要求：** Python 3.10+ / Node.js 18+

### 一键启动（推荐）

```bash
# macOS / Linux
./start.sh

# Windows
start.bat
```

脚本自动完成环境检查 → 虚拟环境 → 依赖安装 → 前端构建 → 准备数据库 → 启动服务。

首次运行若 `.env` 不存在，会从 `.env.example` 复制一份并提示你填写配置。

`.env.example` 默认用 SQLite，所以填好 LLM Key 再跑一次，脚本会自动建好示例库，不需要额外操作。
用 MySQL / PostgreSQL 的话脚本不会碰你的库（`scripts/init_db.sql` 里有 `DROP TABLE`，自动执行太危险），
按下面的「初始化示例数据库」手动导入即可。

启动后访问 **http://localhost:8000**

> 自定义端口：`PORT=9000 ./start.sh`

### 开发模式（前后端分离，热更新）

```bash
./dev.sh
```

- 前端（Vite HMR）：http://localhost:5173
- 后端（uvicorn --reload）：http://localhost:8000

### 手动安装

```bash
# 1. 配置
cp .env.example .env
# 编辑 .env，填入数据库连接和 LLM API Key

# 2. 后端
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-backend.txt

# 3. 前端
cd frontend && npm install && npm run build && cd ..

# 4. 启动
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 初始化示例数据库

Agent 只认 `data/tables.yaml` 白名单里的 9 张表。如果你的库里还没有这些表，可以用仓库自带的脚本生成一份示例数据。

### SQLite（无需安装数据库，最快跑通）

`.env` 里写：

```bash
DB_TYPE=sqlite
DB_SQLITE_PATH=./data/local.db
```

然后跑一次脚本即可（`--out` 省略时自动取 `DB_SQLITE_PATH`）：

```bash
python scripts/init_db.py --dialect sqlite
```

其实用 `./start.sh` 的话这一步是自动的 —— 脚本发现 `.env` 是 sqlite 且库文件不存在，就会建好再启动。

### MySQL

```bash
# 生成导入脚本
python scripts/init_db.py --dialect mysql --out scripts/init_db.sql

# 导入（脚本可重复执行，会先 DROP 再建）
mysql -u root -p your_database < scripts/init_db.sql
```

仓库里已附了一份生成好的 `scripts/init_db.sql`，不想跑 Python 可以直接导入。

### PostgreSQL

脚本目前不生成 PG 方言。可以把 `scripts/init_db.sql` 里的反引号、`AUTO_INCREMENT`、`ENGINE=InnoDB ...` 尾部改掉后导入，或先用 SQLite 体验。

### 示例数据包含什么

9 张表约 3.5 万行：200 个用户、60 个商品、8 个分类、近 12 个月约 3000 笔订单及明细，另有广告投放、用户行为、商品评价、库存流水。

数据以**运行当天**为基准往回推 12 个月，所以无论什么时候生成，趋势图都收在当前月；同一天内重复生成结果完全一致。几处刻意设计是为了让分析类图表有内容可看：订单带季节性波动、各分类销量不均、行为日志构成完整漏斗、库存流水与商品当前库存对得上。

---

## 支持的平台

**数据库：** MySQL · PostgreSQL · SQLite

**LLM Provider：**

| Provider | 说明 |
|---|---|
| SiliconFlow | 性价比高，支持 Qwen / DeepSeek / GLM 等开源模型 |
| OpenAI | 官方及所有兼容接口（Ollama、vLLM 等） |
| 通义千问 | 阿里云 DashScope |
| 阿里云百炼 | 百炼平台模型 |

---

## Web 界面

### 单 Agent 查询

自然语言输入 → SQL → 结果表格 + 置信度。适合快速即席查询。

### 多 Agent 智能分析

完整流水线，每次分析含 5 个 Agent 依次协作，输出交互图表 + 分析结论 + 质量评分，全部记录到历史。

### 思考过程面板

实时流式展示每个 Agent 的决策日志：

```
🔍 [Planner Agent] 正在分析问题意图...
   ✅ 意图：分析各月销售趋势
   📊 建议图表：line

💬 [SQL Agent] 准备执行查询...
   ✅ SQL：SELECT DATE_FORMAT(...), SUM(...) GROUP BY ...
   置信度：90%，返回 7 行

📊 [Chart Agent] 正在判断图表类型...
   ✅ 图表类型：line

📝 [Summary Agent] 正在生成分析结论...
   ✅ 结论已生成（87 字）

🏅 [LLM-as-Judge] 正在评估输出质量...
   SQL 准确性：9/10  图表适配：9/10  结论质量：8/10
```

### 管理功能

- **配置管理** — 修改数据库连接和 LLM API Key，一键测试连接
- **查询历史** — 全部历史记录，含图表回放，支持搜索与删除
- **表白名单** — 增删允许查询的表
- **Schema 注释** — 编辑字段业务含义，支持 AI 自动生成
- **Agent 参数** — 调整重试次数、置信度阈值等行为参数
- **Few-shot 管理** — 添加问题-SQL 示例对，持续优化准确率

---

## 配置

`.env` 环境变量：

```env
# LLM Provider（选一个即可）
LLM_PROVIDER=siliconflow          # openai | qwen | siliconflow | bailian

# SiliconFlow（推荐，性价比高）
SILICONFLOW_API_KEY=sk-xxx
SILICONFLOW_MODEL=Pro/zai-org/GLM-5.1

# OpenAI
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# 数据库
DB_TYPE=mysql                     # mysql | postgresql | sqlite
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=your_database

# Agent 参数
MAX_RETRY=3
CONFIDENCE_THRESHOLD=0.6
```

`data/tables.yaml` — 白名单表，`data/schema_annotations.yaml` — 字段语义注释，均可在 Web UI 中直接编辑。

---

## SDK 用法

### 单 Agent

```python
from sql_agent import build_agent

agent = build_agent()
result = agent.query("上个月销售额最高的商品是什么？")

if result.success:
    print(result.sql)
    print(result.formatted_table)
elif result.need_confirm:
    print(f"置信度较低，请确认 SQL：\n{result.sql}")
else:
    print(f"查询失败：{result.error}")
```

### 多 Agent

```python
from sql_agent import build_pipeline

pipeline = build_pipeline()
state = pipeline.invoke({"question": "各月销售额趋势如何？"})

print(state["sql_results"][0]["sql"])    # 生成的 SQL
print(state["summary"])                  # 分析结论
print(state["judge_scores"])             # {"sql_correctness": 9, "chart_fitness": 9, "summary_quality": 8}
```

---

## 流水线架构

```
用户问题
    │
    ▼
┌─────────────┐
│   Planner   │  意图拆解 → intent / chart_hint / sub_questions
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  SQL Agent  │  ReAct 循环 → 生成 → 校验 → 执行 → 自愈重试
└──────┬──────┘
       │ 成功                 失败 → END
       ▼
┌─────────────┐
│ Chart Agent │  规则推断 → Plotly 交互图表
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Summary    │  数据摘要 + 意图 → 2-4 句中文结论
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ LLM-as-Judge│  三维度评分 → 写入查询历史
└─────────────┘
```

---

## 项目结构

```
sql-agent-kit/
├── start.sh / start.bat          # 一键启动
├── dev.sh                        # 开发模式
├── backend/                      # FastAPI 后端
│   ├── main.py                   # 入口
│   ├── routers/                  # API 路由
│   └── services/                 # Agent 服务 + SSE 流式推送
├── frontend/                     # Vue 3 SPA
│   └── src/views/                # 8 个页面
├── sql_agent/                    # 核心 Agent 包（不依赖 Web 层）
│   ├── single/                   # 单 Agent 主链路
│   ├── multi/                    # 多 Agent 节点 (planner / sql / chart / summary / judge)
│   ├── graph/pipeline.py         # LangGraph 编排
│   ├── llm/                      # LLM 客户端（OpenAI / Qwen / SiliconFlow / Bailian）
│   ├── schema/                   # Schema 加载与注释
│   ├── executor/                 # SQL 执行器
│   ├── validator/                # 安全 / 语法 / 置信度校验
│   ├── fewshot/                  # Few-shot 存储与检索
│   └── feedback/                 # 查询日志
├── data/                         # 白名单 + Schema 注释配置
├── config/settings.yaml          # 全局参数
└── Dockerfile
```

---

## Docker 部署

```bash
# 构建
docker build -t sql-agent-kit .

# 运行
docker run -d \
  --name sql-agent-kit \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  sql-agent-kit
```

使用 SQLite 时额外挂载数据库文件：`-v $(pwd)/data/local.db:/app/data/local.db`

---

## License

MIT — 自由使用、修改和分发。

---

## 联系

- 邮箱：ly956501819@foxmail.com
- 微信：ly956501819
