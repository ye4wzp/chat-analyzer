# Chat Analyzer - 聊天记录智能分析系统

## 项目位置

```
chat-analyzer/
```

## 项目概述

个人聊天记录聚合分析工具，支持微信、QQ、Telegram 多平台消息导入，使用 LLM 对消息进行智能分类、紧急度评估和摘要总结。

## 技术栈

| 层 | 技术 |
|---|------|
| 后端 | Python 3.12 + FastAPI + aiosqlite |
| 前端 | React + TypeScript + Vite + shadcn/ui |
| LLM | Claude CLI / OpenAI 兼容 API（LM Studio / Ollama）|
| 数据库 | SQLite（本地文件）|

## 目录结构

```
chat-analyzer/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口，所有 API 端点
│   │   ├── core/
│   │   │   ├── config.py         # 配置模型（LLM、筛选规则等）
│   │   │   └── database.py       # 数据库初始化、路径
│   │   └── services/
│   │       ├── analyzer.py       # LLM 分析引擎（分类 + 总结）
│   │       ├── pre_filter.py     # 消息预过滤（正则去噪）
│   │       └── sync/
│   │           ├── wechat.py     # 微信消息同步（wx-cli）
│   │           ├── qq.py         # QQ 导入（待解锁）
│   │           └── telegram.py   # Telegram 导入
│   ├── config/                   # 配置文件存储
│   └── .venv/                    # Python 虚拟环境
├── frontend/
│   ├── src/
│   │   ├── lib/api.ts            # API 调用封装
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx     # 首页仪表盘
│   │   │   ├── Chats.tsx         # 聊天列表（多选+批量操作）
│   │   │   ├── Messages.tsx      # 消息列表（筛选+分析结果）
│   │   │   ├── Settings.tsx      # 设置（LLM配置/同步/分析）
│   │   │   └── Login.tsx         # 登录页
│   │   └── components/ui/        # shadcn/ui 组件
│   └── dist/                     # 构建产物
└── PROJECT.md                    # 本文件
```

## 启动方式

```bash
# 后端
cd chat-analyzer/backend
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端（开发模式）
cd chat-analyzer/frontend
npm run dev

# 前端（构建，后端自动 serve）
cd chat-analyzer/frontend
npm run build
```

启动后端后访问 http://localhost:8000 即可。

## 核心功能

### 1. 消息导入

| 平台 | 方式 | 状态 |
|------|------|------|
| 微信 | wx-cli 命令行工具，自动同步会话和历史消息 | ✅ 可用 |
| Telegram | Telegram Desktop 导出的 JSON 文件 | ✅ 可用 |
| QQ | 本地数据库加密（SQLCipher），需密钥 | ⏳ 待解决 |

### 2. LLM 分析流程

```
消息列表 → 预过滤（正则去噪） → 时间窗口分割 → 逐窗口 LLM 分类 → 整体总结生成
```

- **预过滤**：去除纯表情、单字回复、系统消息
- **窗口分割**：按 30 分钟间隔切分，大窗口重叠滑动
- **分类**：每条消息分类为 important/todo/casual，打紧急度 1-5
- **总结**：AI 生成整体摘要、关键要点、待办事项

### 3. 分析结果审核

分析完成后不会自动入库，而是：
1. 弹出结果预览表格，展示每条消息的分类、紧急度、摘要
2. 顶部显示 AI 生成的聊天总结
3. 用户勾选要保留的消息
4. 点击"保存已选"才入库

### 4. 定时自动分析

在设置页可开启定时任务：
- **自动同步微信**：配置间隔（分钟），定时增量同步新消息
- **自动分析**：配置间隔（分钟），定时分析新消息并提取知识点
- 显示上次/下次执行时间
- 自动跳过正在运行的同类型任务，避免冲突

### 5. 消息时间线视图

侧边栏"时间线"页面，可视化展示消息：
- 左栏：聊天列表 + 平台筛选 + 日期范围
- 右栏：按天分组的垂直时间线，消息卡片含头像、发送者、时间、分析标记
- 分页加载更多

### 6. 支持的 LLM 后端

| 后端 | 说明 |
|------|------|
| Claude CLI | 本地 `claude -p` 命令，需 Claude Code 已认证 |
| OpenAI 兼容 API | LM Studio（端口 1234）或 Ollama（端口 11434） |

### 7. 前端页面

- **首页**：统计概览（消息数、聊天数、重要/待办数）+ 平台分布 + 最近重要消息
- **聊天列表**：按聊天/联系人为维度，支持多选、搜索、平台筛选
- **时间线**：按天分组的可视化消息时间线，支持筛选和分页
- **知识库**：知识点列表、搜索、标签筛选、AI 扩展、编辑、导出 Markdown
- **设置**：LLM 配置、同步/导入、定时任务配置

## API 端点

```
GET  /api/dashboard          # 仪表盘统计
GET  /api/messages           # 消息列表（支持筛选）
GET  /api/chats              # 聊天列表
GET  /api/search             # 关键词搜索
GET  /api/config             # 获取配置
PUT  /api/config             # 更新配置
GET  /api/llm/models         # 获取 LLM 模型列表
GET  /api/llm/test           # 测试 LLM 连接
POST /api/analyze            # 启动分析任务
GET  /api/analyze/{id}/results  # 获取分析结果（审核用）
POST /api/analyze/{id}/confirm  # 确认保存选中的结果
POST /api/sync/wechat        # 同步微信
GET  /api/scheduler           # 获取定时任务配置
PUT  /api/scheduler           # 更新定时任务配置
POST /api/import/telegram    # 导入 Telegram
POST /api/import/qq          # 导入 QQ（待解锁）
GET  /api/tasks/{id}/events  # SSE 任务进度
```

## 数据存储

- 数据库：`~/.chat-analyzer/data/chat.db`
- 配置：`~/.chat-analyzer/config.json`

## 已知问题

- QQ 聊天记录因 SQLCipher 加密无法读取，macOS SIP 阻止提取密钥
- Ollama 小模型（3B）分析质量较低，建议使用 14B+ 模型
- LM Studio localhost 连接问题已通过改用 127.0.0.1 解决（IPv6 兼容性）
