"""Local drafts, manual editing and explicit review for private contacts."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import contact_dossier as svc
from app.services import contact_dossier_analysis as analysis
from app.core import database
from app.core.tasks import create_task, spawn_cancellable, cancel_task

router = APIRouter()
Platform = Literal['wechat', 'qq', 'telegram']


class GenerateRequest(BaseModel):
    contact_sender_key: str = Field(min_length=1, max_length=300)
    resume_run_id: str | None = Field(default=None, max_length=64)


class NoteUpdate(BaseModel):
    version: int = Field(ge=0)
    short_note: str = Field(default='', max_length=80)
    detail_note: str = Field(default='', max_length=1500)
    short_locked: bool = True
    detail_locked: bool = True


class Decision(BaseModel):
    index: int = Field(ge=0, le=6)
    action: Literal['accept', 'reject']
    value: str | None = Field(default=None, max_length=1500)


class ReviewRequest(BaseModel):
    decisions: list[Decision] = Field(max_length=7)


@router.get('/api/contacts/{platform}/{chat_id}/dossier')
async def get_dossier(platform: Platform, chat_id: str):
    return await svc.load_dossier(platform, chat_id)


@router.post('/api/contacts/{platform}/{chat_id}/dossier/generate')
async def generate_dossier(platform: Platform, chat_id: str, body: GenerateRequest):
    run_id = await analysis.prepare(platform, chat_id, body.contact_sender_key, body.resume_run_id)
    task_id = create_task('contact_dossier')
    await analysis.update_run(run_id, task_id=task_id)

    async def work():
        try:
            await analysis.execute(run_id, task_id)
        except Exception:
            pass  # execute persists a safe error and keeps every successful checkpoint.

    spawn_cancellable(task_id, work())
    return await svc.load_dossier(platform, chat_id)


@router.get('/api/contacts/{platform}/{chat_id}/dossier/analysis')
async def get_analysis(platform: Platform, chat_id: str):
    async with database.connect_db() as db:
        return await analysis.latest_job(db, platform, chat_id)


@router.post('/api/contacts/{platform}/{chat_id}/dossier/analysis/stop')
async def stop_analysis(platform: Platform, chat_id: str):
    async with database.connect_db() as db:
        job = await analysis.latest_job(db, platform, chat_id)
    if not job or job['status'] not in analysis.ACTIVE or not job['task_id'] or not cancel_task(job['task_id']):
        raise HTTPException(409, '没有可停止的运行中任务，请刷新查看')
    return {'ok': True}


@router.put('/api/contacts/{platform}/{chat_id}/dossier')
async def save_notes(platform: Platform, chat_id: str, body: NoteUpdate):
    return await svc.save_manual(platform, chat_id, body)


@router.post('/api/contacts/{platform}/{chat_id}/dossier/drafts/{draft_id}/review')
async def review_draft(platform: Platform, chat_id: str, draft_id: str, body: ReviewRequest):
    return await svc.review(platform, chat_id, draft_id, body.decisions)
