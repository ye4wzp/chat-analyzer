# Review Findings Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the review findings so the backend compiles, the frontend builds, imports dedupe, config saves correctly, and analysis results remain usable from messages/search.

**Architecture:** Keep the current local FastAPI + SQLite + React/Vite architecture. Treat the knowledge-base extraction flow as the primary product path, while preserving message-level badges/search by deriving `analysis_results` from confirmed knowledge item sources.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest for backend checks, React 19, TypeScript 6, Vite.

### Task 1: Backend Regression Coverage

**Files:**
- Create: `backend/tests/test_backend_behaviors.py`

**Steps:**
1. Add tests for config update accepting full settings payload.
2. Add tests for VIP pre-filter bypass.
3. Add tests for confirmed knowledge writing `knowledge_items` and derived `analysis_results`.
4. Add tests for message duplicate prevention using platform/chat/message identity.
5. Run `python -m pytest tests -q` and confirm failures before implementation.

### Task 2: Backend Fixes

**Files:**
- Modify: `backend/app/core/database.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/pre_filter.py`
- Modify: `backend/app/services/analyzer.py`
- Modify: `backend/app/services/sync/qq.py`
- Modify: `backend/app/services/sync/wechat.py`
- Modify: `backend/app/services/sync/telegram.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/cli.py`

**Steps:**
1. Add stable dedupe keys to `messages` and `analysis_results`.
2. Make `init_db()` migrate existing local databases forward.
3. Fix config update schema and persistence.
4. Fix VIP filtering and token budget accounting.
5. Replace broken QQ module with a working JSON importer.
6. Save derived message analysis rows when knowledge is confirmed.
7. Update CLI analyze command to save knowledge items instead of calling removed APIs.

### Task 3: Frontend Fixes

**Files:**
- Modify: `frontend/tsconfig.app.json`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/Layout.tsx`
- Modify: `frontend/src/pages/Chats.tsx`
- Modify: `frontend/src/pages/Knowledge.tsx`
- Modify: `frontend/src/pages/Settings.tsx`
- Modify: `frontend/src/pages/settings/LLMTab.tsx`
- Modify: `frontend/src/components/KnowledgeReviewModal.tsx`

**Steps:**
1. Fix TypeScript 6 config compatibility.
2. Align dev proxy with backend port 8000.
3. Remove undefined refs/functions and unused imports/state.
4. Make Settings save payload match backend.
5. Tighten obvious lint/type issues without redesigning UI.

### Task 4: Verification

**Commands:**
- `cd backend && python -m pytest tests -q`
- `cd backend && python -m compileall app`
- `cd frontend && npx tsc -p tsconfig.app.json --noEmit`
- `cd frontend && npm run build`

`npm run lint` should be attempted after build fixes; if project-level lint rules from generated UI utilities remain noisy, report exact residuals.
