# Status Snapshot & Next Steps (2026-05-09)

## 已完成（2026-04-30 plan 全部落地）

- `pytest tests -q` → 4 passed（`tests/test_backend_behaviors.py` 144 行）
- `python -m compileall app` 通过
- `npx tsc -p tsconfig.app.json --noEmit` 通过
- `npm run build` 成功（290ms）

旧 plan `2026-04-30-review-findings-optimization.md` 可视为已归档。

## 真实债务（按优先级）

### P0 — `main.py` 拆分
- 现状：`app/main.py` **941 行**，`app/api/` 与 `app/models/` 仅有空 `__init__.py`
- 动作：按域拆 `api/`（`config`、`messages`、`chats`、`analyze`、`sync`、`scheduler`、`tasks`）+ `models/`（Pydantic 请求/响应），`main.py` 仅保留 `app = FastAPI()`、lifespan、router 装配
- 验收：`main.py` ≤ 100 行；现有测试不改一行通过

### P1 — FastAPI lifespan 迁移
- 现状：`app/main.py:35` 用 `@app.on_event("startup")`，跑测试出 DeprecationWarning
- 动作：换 `@asynccontextmanager` + `FastAPI(lifespan=...)`
- 验收：测试零 warning

### P2 — 前端 code-split
- 现状：`dist/assets/index-*.js` 743 KB（gzip 218 KB），超 Vite 500 KB 阈值
- 动作：路由级 `lazy()` + `Suspense`（`Dashboard` / `Chats` / `Messages` / `Knowledge` / `Timeline` / `Settings` / `Search`）；`shadcn/ui` 与 `recharts` 走独立 manualChunks
- 验收：主 chunk ≤ 300 KB（gzip ≤ 100 KB）

### P3 — 纳入 git
- 现状：整个项目目录 untracked，无版本控制
- 动作：`cd chat-analyzer && git init`；`.gitignore` 屏蔽 `.venv/`、`node_modules/`、`dist/`、`backend/app.db`、`__pycache__/`、`.egg-info/`；首提交按 P0–P2 分批
- 验收：`git status` 干净，`git log` 有有意义的提交边界

## 不在范围

- QQ SQLCipher 解锁（环境限制，PROJECT.md 已标已知）
- 重新设计 UI / 改业务逻辑 / 加新功能
