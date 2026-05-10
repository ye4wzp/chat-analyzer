"""Background scheduler that fires sync/analyze tasks at user-configured intervals."""
import asyncio
import logging
from datetime import datetime, timedelta

from app.core.config import load_config, save_config
from app.core.runners import run_analyze, run_qq_sync, run_sync, run_telegram_sync
from app.core.tasks import create_task, is_task_running, spawn_cancellable
from app.models.analyze import AnalyzeRequest


async def scheduler_loop():
    while True:
        await asyncio.sleep(30)
        try:
            cfg = load_config()
            s = cfg.scheduler
            now = datetime.utcnow()

            if s.sync_enabled and s.sync_interval_minutes > 0 and not is_task_running("sync_wechat"):
                last = datetime.fromisoformat(s.last_sync_at) if s.last_sync_at else None
                if not last or now >= last + timedelta(minutes=s.sync_interval_minutes):
                    task_id = create_task("sync_wechat")
                    spawn_cancellable(task_id, run_sync(task_id, True))
                    cfg.scheduler.last_sync_at = now.isoformat()
                    save_config(cfg)

            if s.qq_enabled and s.qq_interval_minutes > 0 and not is_task_running("sync_qq"):
                last = datetime.fromisoformat(s.last_qq_sync_at) if s.last_qq_sync_at else None
                if not last or now >= last + timedelta(minutes=s.qq_interval_minutes):
                    task_id = create_task("sync_qq")
                    spawn_cancellable(task_id, run_qq_sync(task_id))
                    cfg = load_config()
                    cfg.scheduler.last_qq_sync_at = now.isoformat()
                    save_config(cfg)

            if s.telegram_enabled and s.telegram_interval_minutes > 0 and not is_task_running("sync_telegram"):
                last = datetime.fromisoformat(s.last_telegram_sync_at) if s.last_telegram_sync_at else None
                if not last or now >= last + timedelta(minutes=s.telegram_interval_minutes):
                    task_id = create_task("sync_telegram")
                    spawn_cancellable(task_id, run_telegram_sync(task_id))
                    cfg = load_config()
                    cfg.scheduler.last_telegram_sync_at = now.isoformat()
                    save_config(cfg)

            if s.analyze_enabled and s.analyze_interval_minutes > 0 and not is_task_running("analyze"):
                last = datetime.fromisoformat(s.last_analyze_at) if s.last_analyze_at else None
                if not last or now >= last + timedelta(minutes=s.analyze_interval_minutes):
                    task_id = create_task("analyze")
                    spawn_cancellable(task_id, run_analyze(task_id, AnalyzeRequest()))
                    cfg = load_config()
                    cfg.scheduler.last_analyze_at = now.isoformat()
                    save_config(cfg)
        except Exception as e:
            logging.warning("scheduler loop error: %s", e)
