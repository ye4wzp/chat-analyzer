const API_BASE = "/api"

function apiToken(): string {
  try { return localStorage.getItem("apiToken") || "" } catch { return "" }
}

// EventSource can't send headers — SSE URLs carry the token as a query param.
function sseUrl(path: string): string {
  const token = apiToken()
  if (!token) return `${API_BASE}${path}`
  return `${API_BASE}${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`
}

// Build an /api URL carrying the token as a query param — for browser
// navigations (window.open / <a download>) that can't set the X-API-Token
// header. No-op when no token is configured.
export function apiDownloadUrl(path: string): string {
  const token = apiToken()
  const url = `${API_BASE}${path}`
  if (!token) return url
  return `${url}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`
}

export async function fetchAPI<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = apiToken()
  const res = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-API-Token": token } : {}),
      ...opts?.headers,
    },
  })
  if (!res.ok) {
    const text = await res.text()
    let detail = ""
    try {
      const parsed = JSON.parse(text) as { detail?: unknown }
      if (typeof parsed.detail === "string") detail = parsed.detail
      else if (parsed.detail != null) detail = JSON.stringify(parsed.detail)
    } catch { /* not JSON */ }
    throw new Error(detail || text || res.statusText)
  }
  return res.json()
}

const SSE_MAX_RETRIES = 6

// SSE helper for long-running tasks. Reconnects with capped backoff; onError
// fires only after retries are exhausted. Returns an unsubscribe function.
export function subscribeSSE<T = unknown>(
  path: string,
  onMessage: (data: T) => void,
  onError?: () => void,
): () => void {
  let es: EventSource | null = null
  let attempts = 0
  let timer: number | undefined
  let closed = false

  const connect = () => {
    es = new EventSource(sseUrl(path))
    es.onopen = () => { attempts = 0 }
    es.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data) as T)
      } catch {
        onMessage(e.data as T)
      }
    }
    es.onerror = () => {
      es?.close()
      if (closed) return
      if (attempts >= SSE_MAX_RETRIES) {
        closed = true
        onError?.()
        return
      }
      timer = window.setTimeout(connect, Math.min(1000 * 2 ** attempts, 10_000))
      attempts += 1
    }
  }
  connect()
  return () => {
    closed = true
    window.clearTimeout(timer)
    es?.close()
  }
}

export interface GlobalEventsData {
  tasks: TaskState[]
  unread: number
}

// Session-length /api/events channel: reconnects forever, and polls the REST
// fallbacks every 5s only while the SSE connection is down.
export function subscribeGlobalEvents(onData: (d: GlobalEventsData) => void): () => void {
  let es: EventSource | null = null
  let closed = false
  let attempts = 0
  let retryTimer: number | undefined
  let pollTimer: number | undefined

  const poll = async () => {
    try {
      const [tasks, unread] = await Promise.all([
        fetchAPI<TaskState[]>("/tasks"),
        fetchAPI<{ count: number }>("/inbox/unread_count"),
      ])
      if (!closed) onData({ tasks, unread: unread.count })
    } catch { /* backend down — next tick retries */ }
  }

  const stopPolling = () => {
    if (pollTimer !== undefined) {
      window.clearInterval(pollTimer)
      pollTimer = undefined
    }
  }

  const connect = () => {
    es = new EventSource(sseUrl("/events"))
    es.onopen = () => { attempts = 0; stopPolling() }
    es.onmessage = (e) => {
      try { onData(JSON.parse(e.data) as GlobalEventsData) } catch { /* malformed frame */ }
    }
    es.onerror = () => {
      es?.close()
      if (closed) return
      if (pollTimer === undefined) {
        void poll()
        pollTimer = window.setInterval(() => { void poll() }, 5000)
      }
      retryTimer = window.setTimeout(connect, Math.min(1000 * 2 ** Math.min(attempts, 10), 10_000))
      attempts += 1
    }
  }
  connect()
  return () => {
    closed = true
    window.clearTimeout(retryTimer)
    stopPolling()
    es?.close()
  }
}

export interface Message {
  id: number
  platform: string
  chat_id: string
  chat_name: string
  chat_type: string
  sender_id: string
  sender_name: string
  content: string
  msg_type: string
  timestamp: string
  category?: string
  urgency?: number
  summary?: string
}

export interface AnalysisResult {
  message_id: number
  category: string
  urgency: number
  summary: string
}

export interface ChatInfo {
  platform: string
  chat_id: string
  chat_name: string
  chat_type: string
  msg_count: number
  earliest: string
  latest: string
}

export interface QQConfig {
  enabled: boolean
  host: string
  port: number
  token: string
}

export interface TelegramConfig {
  enabled: boolean
  api_id: number
  api_hash: string
  phone: string
  session_string: string
  username: string
}

export interface Config {
  daily_token_budget: number
  budget_action: string
  api_token?: string  // "********" when set
  vip_contacts: string[]
  self_names?: string[]
  keywords?: string[]
  semantic_triggers?: SemanticTrigger[]
  chat_filter: {
    mode: string
    chats: string[]
  }
  llm: {
    provider: string
    api_url: string
    model: string
    api_key: string
    embedding_model?: string
  }
  wechat?: { account: string }
  qq: QQConfig
  telegram: TelegramConfig
}

export interface LLMModelsResponse {
  provider: string
  models: string[]
}

export interface LLMTestResponse {
  status: string
  provider: string
}

export interface TokenUsage {
  budget: number
  today: {
    prompt_tokens: number
    completion_tokens: number
    total: number
    calls: number
    pct: number
  }
  last_7_days: { day: string; tokens: number }[]
  today_by_purpose: { purpose: string; calls: number; tokens: number }[]
}

export interface DashboardStats {
  total_messages: number
  total_chats: number
  total_knowledge: number
  platforms: { platform: string; count: number }[]
  recent_knowledge: KnowledgeItem[]
  daily_counts?: { date: string; platform: string; count: number }[]
  recent_active?: number  // last 7 days message count
}

export interface KnowledgeItem {
  id: number
  title: string
  content: string
  source_chat: string
  source_message_ids: string
  tags: string
  extended_content?: string
  batch_id: string
  created_at: string
  similarity?: number  // present only on semantic search results
}

export interface PendingKnowledge {
  id: number
  title: string
  content: string
  category: string
  tags: string[]
  source_chat: string
  source_message_ids: string
  urgency: number
  batch_id: string
}

export interface PendingBatch {
  task_id: string
  summary: string | null
  created_at: string
  item_count: number
}

export interface KnowledgeBatch {
  batch_id: string
  item_count: number
  created_at: string
}

export interface SyncHealth {
  platforms: { platform: string; chats: number; last_sync: string | null; last_message_ts: string | null }[]
  stale: { platform: string; chat_id: string; last_sync: string | null }[]
}

export interface AliasEntry {
  id: number
  platform: string
  platform_id: string
  platform_name: string | null
}

export interface AliasGroup {
  canonical_name: string
  entries: AliasEntry[]
}

export interface SemanticTrigger {
  query: string
  threshold: number
}

export interface TaskProgress {
  status: string
  progress: number
  message: string
  type?: string
  result_count?: number
  summary?: string
}

export interface TaskState {
  id: string
  type: string
  status: string
  progress: number
  message: string
  summary?: string
}

export interface AskSource {
  ref: number
  message_id: number
  platform: string
  chat_id: string
  chat_name: string | null
  sender_name: string | null
  timestamp: string
  preview: string
  content: string
}

export interface AskKnowledgeSource {
  ref: string
  id: number
  title: string
  preview: string
  similarity: number
}

export interface AskResponse {
  answer: string
  sources: AskSource[]
  knowledge: AskKnowledgeSource[]
  keywords: string[]
}

export interface ReportMeta {
  id: number
  period: string  // 'weekly' | 'monthly'
  period_start: string
  period_end: string
  created_at: string
}

export interface Report extends ReportMeta {
  content: string
}

export interface SchedulerStatus {
  sync_enabled: boolean
  sync_interval_minutes: number
  last_sync_at: string | null
  next_sync_at: string | null
  qq_enabled: boolean
  qq_interval_minutes: number
  last_qq_sync_at: string | null
  next_qq_sync_at: string | null
  telegram_enabled: boolean
  telegram_interval_minutes: number
  last_telegram_sync_at: string | null
  next_telegram_sync_at: string | null
  analyze_enabled: boolean
  analyze_interval_minutes: number
  last_analyze_at: string | null
  next_analyze_at: string | null
  tag_enabled: boolean
  tag_interval_minutes: number
  last_tag_at: string | null
  next_tag_at: string | null
  tag_max_contacts: number
  todo_remind_enabled: boolean
  todo_remind_interval_minutes: number
  todo_remind_min_urgency: number
  last_todo_remind_at: string | null
  next_todo_remind_at: string | null
  report_weekly_enabled: boolean
  last_report_weekly_at: string | null
  next_report_weekly_at: string | null
  report_monthly_enabled: boolean
  last_report_monthly_at: string | null
  next_report_monthly_at: string | null
}

export interface TelegramStatus {
  logged_in: boolean
  username?: string
  first_name?: string
  user_id?: number
  error?: string
}

export interface TelegramLoginStartResponse {
  phone_code_hash: string
}

export interface TelegramLoginConfirmResponse {
  username?: string
  first_name?: string
  user_id?: number
  need_password?: boolean
}

export interface TelegramQRStartResponse {
  qr_id: string
  url: string
  qr_png: string  // data:image/png;base64,... ready to drop into <img src=...>
  expires_at: number
}

export interface TelegramQRStatusResponse {
  status: "pending" | "password_needed" | "success" | "expired" | "error"
  username?: string
  first_name?: string
  user_id?: number
  error?: string
}

export interface QQTestResponse {
  ok: boolean
  nick: string
  uin: string
}

export interface QQLauncherStatus {
  docker: {
    installed: boolean
    daemon: boolean
    compose: boolean
    server_version?: string
    arch?: string
    error?: string
  }
  installed: boolean
  installed_version: string | null
  container: string | null  // null | "running" | "exited" | "created" | ...
  webui_url: string
  qce_url: string
  token_captured: boolean
  qq_enabled: boolean
}

export interface ContactTag {
  id: number
  name: string
  color: string | null
  source: string  // 'preset' | 'ai'
  status: string  // 'active' | 'pending'
  created_at: string
  confirmed_count: number
  suggested_count: number
}

export interface TagSuggestion {
  link_id: number
  platform: string
  chat_id: string
  confidence: number
  reason: string
  batch_id: string
  created_at: string
  tag_id: number
  tag_name: string
  tag_status: string
  contact_name: string | null
}

export interface ContactTagLink {
  link_id: number
  confidence: number | null
  reason: string | null
  source: string
  status: string  // 'suggested' | 'confirmed'
  batch_id: string | null
  tag_id: number
  name: string
  color: string | null
}

export interface ContactTagEntry {
  platform: string
  chat_id: string
  tag_id: number
  name: string
  color: string | null
}

export interface TodoItem {
  id: number
  urgency: number
  summary: string
  action_items: string[]
  done: boolean
  analyzed_at: string
  platform: string
  chat_id: string
  chat_name: string
  content: string
  timestamp: string
}

export interface TodoStats {
  total: number
  open: number
  urgent: number
}

/* ---------------------------------------------------------------------- */
/* Pre-aggregated statistics (/api/stats/*)                                 */
/* ---------------------------------------------------------------------- */

export interface StatsStatus {
  ready: boolean
  version_ok: boolean
  stale: boolean
  last_message_id: number
  max_message_id: number
  behind: number
  built_at: string | null
  running: boolean
}

export interface StatsAccount {
  platform: string
  account: string
  messages: number
  chats: number
}

export interface KindCount { kind: string; label: string; count: number }
export interface MonthCount { month: string; count: number }
export interface DayCount { day: string; count: number }

export interface TopChat {
  platform: string
  chat_id: string
  chat_name: string | null
  chat_type: string | null
  account: string
  count: number
  last_ts: string | null
}

export interface StatsOverview {
  totals: {
    messages: number
    chats: number
    groups: number
    privates: number
    active_months: number
    first_ts: string | null
    last_ts: string | null
  }
  monthly: MonthCount[]
  hourly: number[]
  weekday: number[]
  kinds: KindCount[]
  platforms: { platform: string; count: number; chats: number }[]
  top_chats: TopChat[]
}

export interface StatsCalendar {
  year: number | null
  years: number[]
  days: DayCount[]
  total: number
  active_days: number
  peak: DayCount | null
}

export interface YearlyChat {
  platform: string
  chat_id: string
  chat_name: string | null
  chat_type: string | null
  count: number
  active_days: number
}

export interface StatsYearly {
  year: number | null
  years: number[]
  totals: {
    messages: number
    prev_messages: number
    delta_pct: number | null
    active_days: number
    avg_per_active_day: number
    chats: number
    groups: number
    new_chats: number
    new_groups: number
  } | null
  monthly: MonthCount[]
  hourly: number[]
  weekday: number[]
  kinds: KindCount[]
  days: DayCount[]
  peak_day: DayCount | null
  peak_month: MonthCount | null
  peak_hour: number | null
  streak: { days: number; start: string | null; end: string | null }
  top_chats: YearlyChat[]
  top_groups: YearlyChat[]
  top_privates: YearlyChat[]
  top_speakers: { platform: string; chat_id: string; chat_name: string | null; name: string; count: number }[]
  resources: Record<string, number>
}

export interface ChatStats {
  platform: string
  chat_id: string
  chat_name: string | null
  chat_type: string | null
  messages: number
  first_ts: string | null
  last_ts: string | null
  distinct_senders: number
  monthly: MonthCount[]
  hourly: number[]
  weekday: number[]
  kinds: KindCount[]
  recent_days: DayCount[]
}

export interface MemberStat {
  sender_key: string
  name: string
  count: number
  share: number
  first_ts: string | null
  last_ts: string | null
  active_months: number
  hours: number[]
  peak_hour: number | null
  night_ratio: number
}

export interface MembersResponse {
  platform: string
  chat_id: string
  total_messages: number
  member_count: number
  members: MemberStat[]
  trend: Record<string, string | number>[]
  trend_keys: { sender_key: string; name: string }[]
  monthly_active: { month: string; members: number; count: number }[]
  inactive: { name: string; sender_key: string; count: number; last_ts: string | null }[]
  inactive_days: number
}

export interface ContactListItem {
  platform: string
  chat_id: string
  chat_name: string | null
  chat_type: string | null
  account: string
  messages: number
  first_ts: string | null
  last_ts: string | null
  tags: { tag_id: number; name: string; color: string | null }[]
  canonical_name: string | null
  is_vip: boolean
}

export interface ContactProfile {
  platform: string
  chat_id: string
  chat_name: string
  chat_type: string | null
  messages: number
  first_ts: string | null
  last_ts: string | null
  active_days: number
  distinct_senders: number
  span_days: number | null
  active_months: number
  monthly: MonthCount[]
  hourly: number[]
  weekday: number[]
  kinds: KindCount[]
  senders: { sender_key: string; name: string; count: number; share: number; last_ts: string | null; is_me: boolean; is_contact: boolean }[]
  tags: ContactTagLink[]
  canonical_name: string | null
  aliases: (AliasEntry & { canonical_name: string })[]
  is_vip: boolean
  summary: string | null
  summary_generated_at: string | null
  knowledge: { id: number; title: string; content: string; tags: string; created_at: string }[]
  todos: { total: number; open: number }
  resources: Record<string, number>
  recent_messages: { id: number; sender_name: string | null; content: string; msg_type: string | null; timestamp: string }[]
}

export interface ResourceItem {
  id: number
  message_id: number
  platform: string
  chat_id: string
  chat_name: string | null
  sender_name: string | null
  timestamp: string
  kind: string
  url: string | null
  title: string | null
}

export interface ResourcesResponse {
  items: ResourceItem[]
  has_more: boolean
  counts: Record<string, number>
}

export function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
