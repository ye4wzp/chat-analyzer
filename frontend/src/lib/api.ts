const API_BASE = "/api"

export async function fetchAPI<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...opts?.headers },
    ...opts,
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(err || res.statusText)
  }
  return res.json()
}

// SSE helper for long-running tasks
export function subscribeSSE<T = unknown>(
  path: string,
  onMessage: (data: T) => void,
  onError?: (err: Event) => void,
): EventSource {
  const es = new EventSource(`${API_BASE}${path}`)
  es.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data) as T)
    } catch {
      onMessage(e.data as T)
    }
  }
  es.onerror = (e) => {
    es.close()
    onError?.(e)
  }
  return es
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
  vip_contacts: string[]
  keywords?: string[]
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
  title: string
  content: string
  tags: string[]
  source_chat: string
  source_message_ids: string
  urgency: number
  batch_id: string
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

export function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
