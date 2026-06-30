# Chat Analyzer

[中文](README.md) · **English**

> A local-first tool that aggregates your WeChat / QQ / Telegram chat history into one SQLite database and uses an LLM to extract knowledge, tag contacts, and surface to-dos — entirely on your machine. The AI only suggests; you decide.

![Dashboard](docs/screenshots/dashboard-dark.png)

---

## ✨ Features

- **Multi-platform aggregation** — WeChat (wx-cli), QQ (NapCat + QCE), and Telegram (Telethon account API) messages land in a single SQLite database; platform is just a column, not a silo.
- **LLM analysis pipeline** — pre-filter denoising → split into 30-minute conversation windows → per-window classification (important / todo / casual + urgency) → overall summary. Results go to a review table first; nothing is saved until you confirm.
- **Knowledge base** — knowledge points extracted from chats, with keyword / semantic search (embeddings), related-item recommendations, AI expansion, editing, and Markdown export.
- **Contact tagging** — the LLM reads a contact's history and auto-tags them (single / batch / scheduled), combining a preset tag library with free-form AI suggestions; pending-review queue, group insights, and one-click "add to denoise allowlist." Cross-platform, in-app only, never written back to WeChat.
- **To-do board** — aggregates to-dos and action items produced by analysis, sorted by urgency with check-off completion.
- **Keyword triggers + inbox** — messages matching your keywords land in an inbox, with macOS notifications.
- **Scheduler** — five independent jobs: sync (WeChat / QQ / Telegram), auto-analyze, and auto-tag.
- **Cool Slate design system** — a dark-first "cool terminal" UI with one-click dark / light theme switch, monospace for IDs / timestamps / numbers, and a same-hue chart ramp.
- **Security & usage** — config file at `0600` perms with secrets redacted in responses; daily token budget and usage tracking; automatic SQLite backups.

## 🖼 Screenshots

| Dashboard (dark) | Dashboard (light) |
|---|---|
| ![dark](docs/screenshots/dashboard-dark.png) | ![light](docs/screenshots/dashboard-light.png) |

| Contact tags | Knowledge base |
|---|---|
| ![tags](docs/screenshots/tags.png) | ![knowledge](docs/screenshots/knowledge.png) |

## 🧱 Tech stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12 · FastAPI · Uvicorn · aiosqlite · httpx |
| Frontend | React 18 · TypeScript · Vite · Tailwind CSS v4 · shadcn-style components · Recharts |
| LLM | Claude CLI (`claude -p`) / Codex CLI / OpenAI-compatible API (LM Studio · Ollama) |
| Database | SQLite (`~/.chat-analyzer/data/chat.db`) |
| Connectors | WeChat: wx-cli ｜ QQ: NapCat + qq-chat-exporter ｜ Telegram: Telethon |

## 🚀 Quick start

Prerequisites: Python 3.12, Node 18+, and at least one working LLM backend (an authenticated Claude CLI, or a local LM Studio / Ollama).

```bash
# 1. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. Frontend (dev, hot reload)
cd frontend
npm install
npm run dev          # http://localhost:5173

# Or: build and let the backend serve it on a single port
npm run build        # then open http://localhost:8000
```

Connect data sources (WeChat / QQ / Telegram) and pick an LLM under **Settings → Data Sources / AI Model**. See the [Chinese project guide](chat-analyzer-项目说明.md) for full details.

## 🗂 Data storage

| What | Path |
|---|---|
| Database | `~/.chat-analyzer/data/chat.db` |
| Config (`0600` perms) | `~/.chat-analyzer/config.json` |

> The config holds API keys / QQ token / Telegram session and lives **in your home directory, outside the repo** — it never enters git.

## 🔒 Privacy

Runs entirely on your machine: messages, config, and analysis results never leave your computer, and you can use local models (LM Studio / Ollama) to stay fully offline. All AI output passes through human review before it is saved.

## 📄 Docs

- [`chat-analyzer-项目说明.md`](chat-analyzer-项目说明.md) — architecture, data flow, full API endpoints, setup (Chinese)
- [`PROJECT.md`](PROJECT.md) — earlier project notes

## License

[MIT](LICENSE)
