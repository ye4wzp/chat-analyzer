import { useCallback, useEffect, useRef, useState } from "react"
import { Loader2, Sparkles } from "lucide-react"
import { fetchAPI, getErrorMessage } from "@/lib/api"
import { formatTime } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { SourceMessages } from "@/components/SourceMessages"

type Kind = "tag" | "short_note" | "detail_note"
interface Suggestion { kind: Kind; value: string; source_ids: number[]; basis: string; reason: string; edited?: boolean }
interface Current {
  version: number; short_note: string; detail_note: string; short_locked: boolean; detail_locked: boolean
  contact_sender_key: string | null; updated_at: string | null
  evidence: Record<string, Suggestion>; tags: { id: number; name: string; reason: string | null }[]
}
interface Draft {
  id: string; base_version: number; created_at: string; items: Suggestion[]; contact_sender_key: string
  coverage: { mode?: string; processed?: number; sampled?: number; total: number; first: string; last: string; target_sampled?: number; target_processed?: number; suppressed: number; batches?: number }
}
interface AnalysisJob {
  id: string; status: string; stage: string; processed: number; total: number; completed_batches: number; total_batches: number
  error: string | null; first: string; last: string; created_at: string; contact_sender_key: string
  model: { provider: string; model: string }
}
interface Dossier { current: Current; draft: Draft | null; job: AnalysisJob | null; senders: { key: string; name: string | null; count: number }[] }
const LABEL: Record<Kind, string> = { tag: "标签", short_note: "简短备注", detail_note: "详细备注" }
const areaClass = "min-h-24 w-full rounded-md border border-border bg-background p-3 text-sm leading-relaxed disabled:opacity-60"

function Evidence({ item }: { item?: Suggestion }) {
  if (!item) return null
  return <div className="mt-2 text-xs text-muted-foreground">
    <p>{item.edited ? "人工修改后采纳 · " : ""}{item.basis === "self_report" ? "对方自述" : item.basis === "interaction" ? "互动记录" : "待核实"} · {item.reason}</p>
    <SourceMessages key={item.source_ids.join(",")} sourceIds={JSON.stringify(item.source_ids)} />
  </div>
}

export function ContactDossier({ platform, chatId, legacyNotes, tags, onChanged, onDirtyChange }: {
  platform: string; chatId: string; legacyNotes?: string | null; tags: { id: number; name: string }[]; onChanged: () => void; onDirtyChange: (dirty: boolean) => void
}) {
  const path = `/contacts/${platform}/${encodeURIComponent(chatId)}/dossier`
  const [data, setData] = useState<Dossier | null>(null)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState(false)
  const [sender, setSender] = useState("")
  const [short, setShort] = useState("")
  const [detail, setDetail] = useState("")
  const [shortLock, setShortLock] = useState(false)
  const [detailLock, setDetailLock] = useState(false)
  const [choices, setChoices] = useState<Record<number, boolean>>({})
  const [edits, setEdits] = useState<Record<number, string>>({})
  const [job, setJob] = useState<AnalysisJob | null>(null)
  const [completedJob, setCompletedJob] = useState("")
  const mounted = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const dirty = !!data && (short !== data.current.short_note || detail !== data.current.detail_note || shortLock !== data.current.short_locked || detailLock !== data.current.detail_locked)
  const reviewDirty = !!data?.draft?.items.some((item, index) => choices[index] || (edits[index] ?? item.value) !== item.value)
  useEffect(() => {
    onDirtyChange(dirty || reviewDirty)
    return () => onDirtyChange(false)
  }, [dirty, reviewDirty, onDirtyChange])
  useEffect(() => {
    if (!dirty && !reviewDirty) return
    const unload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = "" }
    const navigate = (e: MouseEvent) => {
      const link = (e.target as Element | null)?.closest?.("a[href]")
      if (link && !window.confirm("有尚未保存的备注或审核选择，离开将丢弃这些编辑。确定离开？")) {
        e.preventDefault(); e.stopPropagation()
      }
    }
    window.addEventListener("beforeunload", unload)
    document.addEventListener("click", navigate, true)
    return () => { window.removeEventListener("beforeunload", unload); document.removeEventListener("click", navigate, true) }
  }, [dirty, reviewDirty])
  const apply = useCallback((next: Dossier) => {
    setData(next); setShort(next.current.short_note); setDetail(next.current.detail_note)
    setShortLock(next.current.short_locked); setDetailLock(next.current.detail_locked)
    setSender(previous => previous || next.job?.contact_sender_key || next.draft?.contact_sender_key || next.current.contact_sender_key || "")
    setChoices({}); setEdits({})
    setJob(next.job); setCompletedJob("")
  }, [])
  useEffect(() => {
    let active = true
    fetchAPI<Dossier>(path).then(next => { if (active) apply(next) }).catch(e => { if (active) setError(getErrorMessage(e)) })
    return () => { active = false }
  }, [path, apply])
  const running = !!job && ["pending", "running"].includes(job.status)
  useEffect(() => {
    if (!running) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await fetchAPI<AnalysisJob | null>(`${path}/analysis`)
        if (!active) return
        setJob(next)
        if (next?.status === "done") setCompletedJob(next.id)
        if (!next || !["pending", "running"].includes(next.status)) return
      } catch { /* Preserve the job while the backend is temporarily unavailable. */ }
      if (active) timer = setTimeout(() => { void poll() }, 2000)
    }
    timer = setTimeout(() => { void poll() }, 1000)
    return () => { active = false; clearTimeout(timer) }
  }, [path, running])
  useEffect(() => {
    if (!completedJob || dirty || reviewDirty || busy) return
    let active = true
    fetchAPI<Dossier>(path).then(next => {
      if (active) { apply(next); setNotice("全量分析完成，草稿尚未生效，请逐项审核。") }
    }).catch(e => { if (active) setError(getErrorMessage(e)) })
    return () => { active = false }
  }, [completedJob, dirty, reviewDirty, busy, path, apply])
  const act = async (url: string, method: string, body: unknown, message: string) => {
    setBusy(true); setError(""); setNotice("")
    try {
      const next = await fetchAPI<Dossier>(url, { method, body: JSON.stringify(body) })
      if (!mounted.current) return
      apply(next); setNotice(message); onChanged()
    } catch (e) { if (mounted.current) setError(getErrorMessage(e)) }
    finally { if (mounted.current) setBusy(false) }
  }
  const refresh = async () => {
    setBusy(true); setError("")
    try { const next = await fetchAPI<Dossier>(path); if (mounted.current) apply(next) } catch (e) { if (mounted.current) setError(getErrorMessage(e)) }
    finally { if (mounted.current) setBusy(false) }
  }
  const stop = async () => {
    setBusy(true); setError("")
    try {
      await fetchAPI(`${path}/analysis/stop`, { method: "POST" })
      if (mounted.current) setNotice("正在停止分析，已完成批次会保留。")
    } catch (e) { if (mounted.current) setError(getErrorMessage(e)) }
    finally { if (mounted.current) setBusy(false) }
  }
  if (!data) return <section className="rounded-lg border border-border p-4">
    <p className="text-sm">{error || "正在读取本地档案..."}</p>
    {error && <Button variant="outline" size="sm" onClick={() => { void refresh() }}>重试</Button>}
  </section>
  const current = data.current
  const draft = data.draft
  const stale = draft && (draft.base_version !== current.version || sender !== draft.contact_sender_key)
  const selected = draft?.items.filter((_, i) => choices[i]).length || 0
  return <section id="contact-dossier" className="space-y-4 rounded-xl border border-border bg-card p-5" aria-label="联系人本地档案">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="text-base font-semibold">标签与备注 · 本地档案</h2>
        <p className="mt-1 text-xs text-muted-foreground">AI 只生成草稿，逐项确认后生效。不会修改聊天软件里的备注。</p>
      </div>
      <Button variant="ghost" size="sm" disabled={busy || dirty || reviewDirty} onClick={() => { void refresh() }}>刷新档案</Button>
    </div>
    {legacyNotes && <p className="rounded bg-secondary p-3 text-sm whitespace-pre-wrap">原有人物备注（保留）：{legacyNotes}</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    {completedJob && (dirty || reviewDirty) && <p className="text-xs text-amber-500">新草稿已生成。先处理当前未提交的编辑，再刷新查看。</p>}
    {(dirty || reviewDirty) && <div className="flex flex-wrap items-center gap-2 text-xs text-amber-500">
      <span>有尚未提交的编辑；保存、审核或放弃后才能刷新和重新生成。</span>
      <Button variant="ghost" size="sm" disabled={busy} onClick={() => apply(data)}>放弃未提交编辑</Button>
    </div>}
    <div className="grid gap-5 xl:grid-cols-2">
      <div className="space-y-3">
        <h3 className="text-sm font-medium">当前生效内容</h3>
        <div className="flex flex-wrap gap-2">
          {tags.length ? tags.map(tag => <details key={tag.id} className="rounded border border-border p-2 text-xs">
            <summary className="cursor-pointer">{tag.name}</summary>
            {current.evidence[`tag:${tag.id}`] ? <Evidence item={current.evidence[`tag:${tag.id}`]} /> : <p className="mt-2 text-muted-foreground">已有标签，暂无本次档案的原文依据</p>}
          </details>) : <p className="text-xs text-muted-foreground">暂无已确认标签</p>}
        </div>
        <label className="block space-y-1 text-sm"><span>简短备注</span>
          <Input aria-label="当前简短备注" maxLength={80} value={short} disabled={busy || reviewDirty} onChange={e => { setShort(e.target.value); setShortLock(true) }} placeholder="一句话记住对方是谁" />
        </label>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={shortLock} disabled={busy || reviewDirty} onChange={e => setShortLock(e.target.checked)} />锁定简短备注，AI 不提出替换</label>
        <Evidence item={current.evidence.short_note} />
        <label className="block space-y-1 text-sm"><span>详细备注</span>
          <textarea aria-label="当前详细备注" className={areaClass} maxLength={1500} value={detail} disabled={busy || reviewDirty} onChange={e => { setDetail(e.target.value); setDetailLock(true) }} placeholder="认识来源、明确业务、互动记录、待跟进事项" />
        </label>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={detailLock} disabled={busy || reviewDirty} onChange={e => setDetailLock(e.target.checked)} />锁定详细备注，AI 不提出替换</label>
        <Evidence item={current.evidence.detail_note} />
        <Button size="sm" disabled={busy || !dirty || reviewDirty} onClick={() => { void act(path, "PUT", { version: current.version, short_note: short, detail_note: detail, short_locked: shortLock, detail_locked: detailLock }, "手工备注已保存到本地。") }}>确认保存手工备注</Button>
        {current.updated_at && <p className="text-xs text-muted-foreground">上次保存：{formatTime(current.updated_at)}</p>}
      </div>
      <div className="space-y-3">
        <h3 className="text-sm font-medium">生成与审核草稿</h3>
        <label className="block space-y-1 text-sm"><span>确认哪位发言人是对方</span>
          <Select aria-label="对方发言人" value={sender} disabled={busy || running} onChange={e => setSender(e.target.value)}>
            <option value="">请选择对方，不要选择自己</option>
            {data.senders.map(s => <option key={s.key} value={s.key}>{s.name || "未标注姓名"} · {s.key} · {s.count} 条</option>)}
          </Select>
        </label>
        <p className="text-xs text-muted-foreground">按时间分批读取此私聊的全部已入库文本，长消息完整拆段，再分层汇总标签和备注。图片、语音等只使用已入库文字；不自动合并其他账号或群内身份。</p>
        <p className="text-xs text-muted-foreground">范围固定为任务开始时的本地记录；后续新增消息在下次全量分析时纳入。使用设置中的模型和用量预算。</p>
        <Button variant="outline" size="sm" disabled={busy || running || dirty || reviewDirty || !sender} onClick={() => { void act(`${path}/generate`, "POST", { contact_sender_key: sender }, "全量分析已开始，可离开页面；完成后生成待确认草稿。") }}>
          {busy || running ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />} {job || draft ? "重新全量分析" : "全量分析并生成草稿"}
        </Button>
        {job && <div className="space-y-2 rounded border border-border p-3 text-xs" role="status">
          <p>{running ? job.stage === "extract" ? "正在分批读取" : "正在汇总草稿" : job.status === "done" ? "已完成全量分析" : "分析已停止"} · {job.processed}/{job.total} 条 · 已完成 {job.completed_batches}/{job.total_batches} 批原文</p>
          <progress className="w-full" max={100} value={job.status === "done" ? 100 : job.stage === "extract" ? 80 * job.completed_batches / Math.max(1, job.total_batches) : job.stage === "final" ? 95 : 85} />
          <p className="text-muted-foreground">{formatTime(job.first)} — {formatTime(job.last)} · {job.model.provider} / {job.model.model}</p>
          {job.error && <p className="text-destructive">{job.error}</p>}
          {running && <Button size="sm" variant="outline" disabled={busy} onClick={() => { void stop() }}>停止分析，保留进度</Button>}
          {["error", "cancelled", "interrupted"].includes(job.status) && <Button size="sm" variant="outline" disabled={busy || dirty || reviewDirty || !sender || sender !== job.contact_sender_key} onClick={() => { void act(`${path}/generate`, "POST", { contact_sender_key: sender, resume_run_id: job.id }, "已继续未完成批次，成功批次不会重复分析。") }}>继续未完成批次</Button>}
        </div>}
        {dirty && <p className="text-xs text-amber-500">请先保存左侧手工修改，再生成或审核。</p>}
        {stale && <p className="text-xs text-amber-500">当前备注或对方发言人已变化，请重新生成草稿。</p>}
        {draft && <div className="space-y-3">
          {running && <p className="text-xs text-amber-500">新分析进行中，旧草稿暂不能编辑或审核；分析失败时仍会保留。</p>}
          <p className="text-xs text-muted-foreground">未生效草稿 · {draft.coverage.mode === "full" ? `全量读取 ${draft.coverage.processed}/${draft.coverage.total} 条，其中对方有效文本 ${draft.coverage.target_processed} 条，共 ${draft.coverage.batches} 批` : `旧版抽样 ${draft.coverage.sampled}/${draft.coverage.total} 条，可重新全量分析`}。历史范围 {formatTime(draft.coverage.first)} — {formatTime(draft.coverage.last)}</p>
          <p className="text-xs text-muted-foreground">草稿依据发言人：{data.senders.find(s => s.key === draft.contact_sender_key)?.name || "未标注姓名"} · {draft.contact_sender_key}</p>
          {draft.coverage.suppressed > 0 && <p className="text-xs text-muted-foreground">已过滤 {draft.coverage.suppressed} 条无新证据的已拒绝建议。</p>}
          {!draft.items.length && <p className="rounded bg-secondary p-3 text-sm">本次没有可供审核的新建议，当前档案保持不变。</p>}
          {draft.items.map((item, index) => <div key={`${draft.id}:${index}`} className="space-y-2 rounded-lg border border-border p-3">
            <label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" checked={!!choices[index]} disabled={busy || running || !!stale || dirty} onChange={e => setChoices(c => ({ ...c, [index]: e.target.checked }))} />采纳{LABEL[item.kind]}</label>
            {item.kind !== "tag" && <p className="text-xs text-muted-foreground">当前：{current[item.kind] || "（空）"}</p>}
            {item.kind === "detail_note" ? <textarea aria-label={`建议${LABEL[item.kind]}`} className={areaClass} disabled={busy || running || !!stale || dirty} value={edits[index] ?? item.value} maxLength={1500} onChange={e => setEdits(v => ({ ...v, [index]: e.target.value }))} /> :
              <Input aria-label={`建议${LABEL[item.kind]}`} disabled={busy || running || !!stale || dirty} value={edits[index] ?? item.value} maxLength={item.kind === "tag" ? 20 : 80} onChange={e => setEdits(v => ({ ...v, [index]: e.target.value }))} />}
            <Evidence item={item} />
          </div>)}
          {draft.items.length > 0 && <>
            <p className="text-xs text-muted-foreground">仅勾选项生效；未勾选项记为拒绝。采纳后的备注自动锁定，修改后的文字标记为人工编辑。</p>
            <Button size="sm" disabled={busy || running || dirty || !!stale} onClick={() => { void act(`${path}/drafts/${draft.id}/review`, "POST", { decisions: draft.items.map((_, index) => ({ index, action: choices[index] ? "accept" : "reject", value: edits[index] })) }, selected ? `已采纳 ${selected} 项，其余记为拒绝。` : "已拒绝本轮建议，原备注和标签未变。") }}>{selected ? `确认采纳 ${selected} 项，其余拒绝` : "拒绝本轮全部建议"}</Button>
          </>}
        </div>}
      </div>
    </div>
  </section>
}
