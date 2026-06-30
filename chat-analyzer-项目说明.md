# Chat Analyzer 聊天记录智能分析系统 — 项目说明

> 文档更新日期：2026-06-29

---

## 一、项目位置

```
/Users/xiafanxing/Documents/项目/chat-analyzer/
```

主要子目录：

```
chat-analyzer/
├── backend/                # FastAPI 后端
│   ├── app/
│   │   ├── main.py         # FastAPI 入口、所有 API 端点
│   │   ├── cli.py          # 命令行入口
│   │   ├── core/
│   │   │   ├── config.py   # 配置模型（LLM、筛选、调度、QQ、Telegram）
│   │   │   └── database.py # SQLite 初始化
│   │   ├── api/            # 预留路由分包目录
│   │   ├── models/         # 预留 ORM 目录
│   │   └── services/
│   │       ├── analyzer.py     # LLM 分析引擎（分类 + 总结）
│   │       ├── tagger.py       # ✨ 联系人标签引擎（AI 打标签 + 群体洞察）
│   │       ├── pre_filter.py   # 正则去噪预过滤
│   │       ├── knowledge.py    # 知识库 / 知识点管理
│   │       └── sync/
│   │           ├── _common.py
│   │           ├── wechat.py        # 微信（wx-cli）
│   │           ├── qq.py            # QQ JSON 兜底导入
│   │           ├── qq_qce.py        # ✨ QQ QCE 在线同步（NapCat）
│   │           ├── telegram.py      # Telegram JSON 兜底导入
│   │           └── telegram_live.py # ✨ Telegram Telethon 账号 API 同步
│   ├── tests/
│   ├── pyproject.toml
│   └── .venv/
├── frontend/               # React + Vite 前端
│   ├── src/
│   │   ├── lib/api.ts                  # API 调用封装
│   │   ├── index.css                   # 主题色变量（emerald 深色主题）
│   │   ├── App.tsx / main.tsx
│   │   ├── components/
│   │   │   ├── ErrorBoundary.tsx
│   │   │   ├── GlobalTaskBar.tsx       # ✨ 全局任务栏（顶部进度条）
│   │   │   ├── KnowledgeReviewModal.tsx
│   │   │   ├── MessageBadges.tsx
│   │   │   └── ui/                     # shadcn 风格基础组件
│   │   └── pages/
│   │       ├── Layout.tsx
│   │       ├── Dashboard.tsx           # 首页仪表盘 + 图表
│   │       ├── Chats.tsx               # 聊天列表
│   │       ├── Timeline.tsx            # 消息时间线
│   │       ├── Knowledge.tsx           # 知识库
│   │       ├── Tags.tsx                # ✨ 联系人标签（联系人/待审核/标签库 3 视图）
│   │       ├── Todos.tsx               # ✨ 待办看板
│   │       ├── Search.tsx              # 搜索
│   │       ├── Settings.tsx            # 设置（5 Tab）
│   │       └── settings/
│   │           ├── LLMTab.tsx
│   │           ├── ImportTab.tsx       # 数据源 Tab
│   │           ├── QQCard.tsx          # ✨ QQ QCE 卡片
│   │           └── TelegramCard.tsx    # ✨ Telegram 登录卡片
│   ├── dist/               # 构建产物（后端会 serve 此目录）
│   ├── package.json
│   └── vite.config.ts
├── config/                 # 项目级配置占位
├── docs/plans/             # 实施计划/回顾笔记
└── PROJECT.md              # 项目自带说明（较早版本）
```

---

## 二、技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12 + FastAPI + Uvicorn + aiosqlite + httpx |
| 前端 | React 18 + TypeScript + Vite + Tailwind + shadcn/ui + Recharts + sonner |
| LLM | Claude CLI（`claude -p`）/ OpenAI 兼容 API（LM Studio / Ollama）|
| 数据库 | SQLite（本地文件 `~/.chat-analyzer/data/chat.db`） |
| 微信 | wx-cli 命令行工具 |
| QQ | NapCat + qq-chat-exporter（QCE）HTTP API |
| Telegram | Telethon 1.43.2（账号级 API + StringSession） |

---

## 三、整体思路

个人聊天记录的「**聚合 + AI 提炼**」工具。设计原则：

1. **多平台聚合**：把微信、QQ、Telegram 的消息统一进一个 SQLite，平台是字段不是孤岛。
2. **本地优先**：所有数据落本地，配置文件 `~/.chat-analyzer/config.json` 权限 0600，敏感字段（API Key、QQ token、Telegram session）入库时脱敏返回。
3. **LLM 透明可审核**：分析结果不直接入库，先弹审核表格让用户勾选确认，AI 只是协助、不替用户决策。
4. **增量 + 定时**：所有同步走增量（按 msgSeq / min_id 等指针），调度器统一管理，可独立开关每个平台。
5. **后端单进程一体化**：FastAPI 既提供 API 又 serve 前端 dist，单端口启动；开发期前端 `npm run dev` 走 vite。
6. **代码风格**：精简、企业级、非必要不注释。

数据流：

```
[微信 wx-cli] ─┐
[QQ QCE API]  ─┼─→ sync 模块 → SQLite messages 表
[Telethon]    ─┘                       ↓
                              pre_filter 正则去噪
                                       ↓
                              时间窗口分割（30min）
                                       ↓
                              LLM 分类 + 紧急度 + 摘要
                                       ↓
                              用户审核表格（勾选）
                                       ↓
                              入库为 analyzed_messages / knowledge_points
```

---

## 四、核心功能现状

### 1. 消息导入 / 同步

| 平台 | 方式 | 状态 |
|---|---|---|
| 微信 | wx-cli 命令行，自动同步会话和增量历史 | ✅ 可用 |
| QQ | NapCat + QCE HTTP API（host/port/token）| ✅ 已上线 |
| QQ | 兜底：手工导出的 JSON 导入 | ✅ 保留 |
| Telegram | Telethon 账号 API（api_id + api_hash + 短信验证码 + 可选 2FA），StringSession 持久化 | ✅ 已上线 |
| Telegram | 兜底：Telegram Desktop 导出的 JSON | ✅ 保留 |

> 旧 PROJECT.md 中提到的 QQ SQLCipher 加密读取方案已废弃。新方案靠 NapCat 启动一个本地 NTQQ，QCE 暴露 HTTP，既稳又活跃维护（GitHub: shuakami/qq-chat-exporter，3.3k⭐）。

### 2. LLM 分析流水线

```
原始消息 → pre_filter（去表情/单字回复/系统消息）
      → 30 分钟窗口切分（大窗口重叠滑动）
      → 逐窗口分类：important / todo / casual + 紧急度 1-5
      → 整体总结：摘要 + 关键要点 + 待办列表
      → 弹出审核表 → 用户勾选 → 入库
```

### 3. 联系人标签（AI 自动归类）

按 `(platform, chat_id)` 给每个联系人打标签，**纯应用内、不写回微信**，跨三平台通用。设计与「分析→审核→入库」一脉相承：AI 只建议，用户决策。

```
联系人近期消息 + 现有 active 标签库
      → LLM 打标签（优先复用预设标签，覆盖不到才建新标签）
      → 新标签落为 pending、关联落为 suggested（待审核池）
      → 用户在「标签 → 待审核」勾选确认
      → 关联转 confirmed，引用到的 pending 标签自动激活
```

- **生成方式**：预设标签库 + AI 自由补充结合（新标签进待批准池）。
- **入口**：单联系人「AI 打标签」、顶部「批量打标签」（仅未打标签的私聊好友，跳过群聊）、定时自动打标签（见调度器）。
- **标签的价值兑现**：
  - **群体洞察**：选中某标签 → 对该标签下所有联系人近期消息做 LLM 聚合摘要（"这群人最近都在关心什么"）。
  - **VIP 联动**：一键把某标签下的人加入 `vip_contacts` 去噪白名单。
  - 按标签筛选联系人、标签使用人数统计。

### 4. 待办看板

`analyzer` 已把 `category='todo'` 的消息和 `action_items` 存入 `analysis_results`，待办看板把它们集中呈现：按紧急度排序、可勾选完成（`analysis_results.done` 标志位）、统计待处理/紧急/累计。数据零额外存储。

### 5. 调度器（定时任务）

设置页「定时任务」Tab 可独立配置 5 个任务：

- 自动同步微信（默认 5 分钟）
- 自动同步 QQ（依赖 QCE 在线）
- 自动同步 Telegram（依赖账号已登录）
- 自动分析（默认 ≥ 10 分钟）
- 自动打标签（默认 6 小时；给未打标签的私聊好友生成建议，落入待审核池）

每个任务显示「上次执行 / 下次执行」时间，正在运行的同类型任务会自动跳过避免冲突。

### 6. 前端 UI

- 主题：emerald 薄荷绿 primary + 深黑底，CSS 变量驱动 Recharts。
- Dashboard：4 张 KPI 卡 + 30 天趋势 LineChart（按平台分色）+ 平台分布 PieChart + 最近知识点。
- 全局任务栏：顶部 1px sweep 动效，1.5s 轮询 `/api/tasks`，可展开任务列表。
- 标签页：联系人 / 待审核 / 标签库 3 视图 + 单联系人管理弹窗 + 群体洞察弹窗。
- 待办页：统计卡 + 可勾选清单（乐观更新）。
- 响应式：移动端详情面板和侧栏改为底部抽屉（Sheet 组件）。
- Toast：sonner 替换原手写灰条。
- 设置页：5 Tab（常规 / AI 模型 / 数据源 / 定时任务 / 备份）。

### 7. 安全加固

- `~/.chat-analyzer/config.json` 写入后 `chmod 0600`。
- `GET /api/config` 敏感字段返回 `********`，`PUT` 时 `********` 代表保留原值。
- Telegram 登录处理 2FA（`SessionPasswordNeededError`）和限速（`FloodWaitError` 把秒数 echo 给前端 Toast）。

---

## 五、API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/dashboard` | 仪表盘统计 |
| GET | `/api/messages` | 消息列表（支持筛选） |
| GET | `/api/chats` | 聊天列表 |
| GET | `/api/search` | 关键词搜索 |
| GET / PUT | `/api/config` | 获取 / 更新配置（敏感字段脱敏） |
| GET | `/api/llm/models` | LLM 模型列表 |
| GET | `/api/llm/test` | 测试 LLM 连接 |
| POST | `/api/analyze` | 启动分析任务 |
| GET | `/api/analyze/{id}/results` | 分析结果（审核用） |
| POST | `/api/analyze/{id}/confirm` | 确认保存选中的结果 |
| POST | `/api/sync/wechat` | 同步微信 |
| POST | `/api/qq/test` | 测试 QQ QCE 连接 |
| POST | `/api/sync/qq` | 同步 QQ（QCE） |
| POST | `/api/import/qq` | 导入 QQ JSON（兜底） |
| POST | `/api/telegram/login/start` | TG 登录第一步：发送验证码 |
| POST | `/api/telegram/login/confirm` | TG 登录第二步：填验证码 / 2FA |
| GET | `/api/telegram/status` | TG 当前登录状态 |
| POST | `/api/telegram/logout` | TG 登出 |
| POST | `/api/sync/telegram` | 同步 TG 全部对话 |
| POST | `/api/import/telegram` | 导入 TG JSON（兜底） |
| GET / PUT | `/api/scheduler` | 获取 / 更新定时任务配置 |
| GET | `/api/tasks` | 当前所有任务 |
| GET | `/api/tasks/{id}/events` | SSE 任务进度 |
| GET / POST | `/api/tags` | 标签库列表（含使用计数）/ 新建预设标签 |
| PATCH / DELETE | `/api/tags/{id}` | 改名·改色·批准(status=active) / 删除标签 |
| POST | `/api/tags/suggest/batch` | 批量打标签（后台任务） |
| GET | `/api/tags/suggestions` | 待审核的 AI 建议列表 |
| POST | `/api/tags/confirm` · `/api/tags/reject` | 勾选确认 / 拒绝建议 |
| GET | `/api/tags/{id}/contacts` | 某标签下的已确认联系人 |
| POST | `/api/tags/{id}/insight` | 标签群体洞察（LLM 聚合摘要） |
| POST | `/api/tags/{id}/vip` | 标签联系人批量加/移出去噪白名单 |
| GET | `/api/contacts/tags` | 全部已确认 (联系人→标签) 关联（列表用，免 N+1） |
| GET / POST | `/api/contacts/{platform}/{chat_id}/tags` | 某联系人标签列表 / 手动加标签 |
| POST | `/api/contacts/{platform}/{chat_id}/tags/suggest` | 单联系人 AI 打标签 |
| DELETE | `/api/contacts/{platform}/{chat_id}/tags/{tag_id}` | 移除联系人某标签 |
| GET | `/api/todos` · `/api/todos/stats` | 待办列表（含已完成可选）/ 统计 |
| PATCH | `/api/todos/{id}` | 勾选完成 / 取消完成 |

---

## 六、启动方式

### 运行（生产模式，前端构建后由后端 serve）

```bash
# 1. 构建前端
cd /Users/xiafanxing/Documents/项目/chat-analyzer/frontend
npm run build

# 2. 启动后端
cd /Users/xiafanxing/Documents/项目/chat-analyzer/backend
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

访问 → http://localhost:8000

### 开发模式（前端热更新）

```bash
# 后端
cd backend && source .venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 前端
cd frontend && npm run dev
```

---

## 七、首次配置步骤

### QQ（QCE）

1. 下载启动 NapCat-QCE：<https://github.com/shuakami/qq-chat-exporter/releases>
2. QQ 扫码登录 NapCat
3. 复制 QCE 控制台显示的 token
4. 进 chat-analyzer → 设置 → 数据源 → QQ 卡片 → 填 host/port/token → 测试连接 → 启用 → 立即同步

### Telegram

1. 打开 <https://my.telegram.org/auth>，进 "API development tools" 创建 application，记录 `api_id` 和 `api_hash`
2. chat-analyzer → 设置 → 数据源 → Telegram → 开始登录 → 填 api_id / api_hash / 手机号 → 收验证码 → 完成（2FA 时再填密码）
3. 启用自动同步

### 微信

直接装 wx-cli，在设置页开启「自动同步微信」。

### LLM

设置 → AI 模型 → 选择后端：

- Claude CLI：本地 `claude -p` 已认证即可
- OpenAI 兼容：LM Studio（127.0.0.1:1234）或 Ollama（127.0.0.1:11434），选模型并测试

---

## 八、数据存储

| 内容 | 路径 |
|---|---|
| 数据库 | `~/.chat-analyzer/data/chat.db` |
| 配置 | `~/.chat-analyzer/config.json`（0600 权限） |
| Telegram session | 上面 config 的 `telegram.session_string` 字段 |

标签相关数据表（建表见 `database.py:SCHEMA_SQL`）：

- `contact_tags`：标签字典。`source`(preset/ai) + `status`(active/pending)，只有 active 标签会喂给 LLM。
- `contact_tag_links`：联系人↔标签关联，键 `(platform, chat_id, tag_id)`。`status`(suggested/confirmed)，AI 建议落 suggested、确认转 confirmed。
- `analysis_results.done`：待办看板的完成标志位（0/1，迁移列）。

---

## 九、当前进度（截至 2026-06-29）

✅ 已完成

- 后端单进程一体化、SQLite、增量同步框架
- 微信 wx-cli 同步
- QQ 通过 NapCat-QCE 自动同步（替换 SQLCipher 路线）
- Telegram 通过 Telethon 账号 API 自动同步 + StringSession 持久化
- LLM 分析（Claude CLI + OpenAI 兼容）+ 用户审核表格
- 5 个独立调度任务的定时器（同步×3 / 分析 / 打标签）
- 前端 UI 大改：emerald 主题、Dashboard 图表、全局任务栏、响应式抽屉、5 Tab 设置页
- sonner Toast、shadcn 基础组件、ErrorBoundary
- 配置文件权限和敏感字段脱敏
- 知识库（Knowledge.tsx + knowledge.py）：列表 / 搜索 / 标签 / AI 扩展 / 编辑 / 导出 Markdown
- ✨ 联系人标签（tagger.py + Tags.tsx）：AI 打标签（单个/批量/定时）+ 审核 + 标签库管理 + 群体洞察 + VIP 联动，跨平台、纯应用内
- ✨ 待办看板（todos.py + Todos.tsx）：聚合分析产出的待办，勾选完成 + 统计

⏳ 已知限制 / 可继续优化

- recharts 让 bundle 到 743KB（gzip 218KB），后续可 code-split
- QCE 消息 element 解析采用启发式（textElement 抽内容、其他类型用 `[图片]/[语音]` 占位），如发现某类型解析差，调 `qq_qce.py:_extract_text`
- Telegram 首次同步会拉全部历史，长账号可能触发 FloodWait（已 echo 秒数到前端 Toast）
- Ollama 3B 模型分析质量较低，建议 14B+
- LM Studio 用 `127.0.0.1` 而非 `localhost`（避免 IPv6 解析问题）
- `app/api/`、`app/models/` 目录已创建但未拆分使用，目前所有路由仍在 `main.py` 内

---

## 十、相关参考

- 项目自带说明（较早版本）：`/Users/xiafanxing/Documents/项目/chat-analyzer/PROJECT.md`
- 历史实施计划：`/Users/xiafanxing/Documents/项目/chat-analyzer/docs/plans/`
- 最近一次大改造的实施计划：`~/.claude/plans/synthetic-beaming-kahan.md`
- QCE 项目：<https://github.com/shuakami/qq-chat-exporter>
- Telethon 文档：<https://docs.telethon.dev/>
