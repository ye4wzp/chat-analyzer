import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, type Config, type SchedulerStatus, type SemanticTrigger } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { LLMTab } from "./settings/LLMTab"
import { ImportTab } from "./settings/ImportTab"
import { BackupsPanel } from "./settings/BackupsPanel"
import { StoragePanel } from "./settings/StoragePanel"
import { SyncScopeCard } from "./settings/SyncScopeCard"
import { Loader2, Clock, Plus, Trash2 } from "lucide-react"
import { formatTime } from "@/lib/utils"
import { MASKED } from "@/lib/constants"

const TAB_KEYS = ["general", "model", "sources", "scheduler", "backups"] as const
type TabKey = typeof TAB_KEYS[number]

export default function Settings() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialTab: TabKey = TAB_KEYS.includes(searchParams.get("tab") as TabKey) ? (searchParams.get("tab") as TabKey) : "general"

  const [tab, setTab] = useState<TabKey>(initialTab)
  const [config, setConfig] = useState<Config | null>(null)
  const [error, setError] = useState("")
  const [budget, setBudget] = useState("")
  const [action, setAction] = useState("")
  const [vip, setVip] = useState("")
  const [selfNames, setSelfNames] = useState("")
  const [wechatAccount, setWechatAccount] = useState("")
  const [assigning, setAssigning] = useState(false)
  const [keywords, setKeywords] = useState("")
  const [apiToken, setApiToken] = useState("")
  const [triggers, setTriggers] = useState<SemanticTrigger[]>([])
  const [llmProvider, setLlmProvider] = useState("claude_cli")
  const [llmUrl, setLlmUrl] = useState("http://127.0.0.1:1234/v1")
  const [llmModel, setLlmModel] = useState("")
  const [llmApiKey, setLlmApiKey] = useState("lm-studio")
  const [llmEmbeddingModel, setLlmEmbeddingModel] = useState("")
  const [scheduler, setScheduler] = useState<SchedulerStatus | null>(null)
  const [syncEnabled, setSyncEnabled] = useState(false)
  const [syncInterval, setSyncInterval] = useState(60)
  const [qqEnabled, setQqEnabled] = useState(false)
  const [qqInterval, setQqInterval] = useState(60)
  const [tgEnabled, setTgEnabled] = useState(false)
  const [tgInterval, setTgInterval] = useState(60)
  const [analyzeEnabled, setAnalyzeEnabled] = useState(false)
  const [analyzeInterval, setAnalyzeInterval] = useState(120)
  const [tagEnabled, setTagEnabled] = useState(false)
  const [tagInterval, setTagInterval] = useState(360)
  const [todoRemindEnabled, setTodoRemindEnabled] = useState(false)
  const [todoRemindInterval, setTodoRemindInterval] = useState(1440)
  const [reportWeeklyEnabled, setReportWeeklyEnabled] = useState(false)
  const [reportMonthlyEnabled, setReportMonthlyEnabled] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savingScheduler, setSavingScheduler] = useState(false)

  useEffect(() => {
    fetchAPI<Config>("/config")
      .then(c => {
        setConfig(c)
        setBudget(String(c.daily_token_budget))
        setAction(c.budget_action)
        setVip(c.vip_contacts.join(", "))
        setSelfNames((c.self_names || []).join(", "))
        setWechatAccount(c.wechat?.account || "")
        setKeywords((c.keywords || []).join(", "))
        setApiToken(c.api_token || "")
        setTriggers(c.semantic_triggers || [])
        setLlmProvider(c.llm.provider)
        setLlmUrl(c.llm.api_url)
        setLlmModel(c.llm.model)
        setLlmApiKey(c.llm.api_key)
        setLlmEmbeddingModel(c.llm.embedding_model || "")
      })
      .catch(e => setError(getErrorMessage(e)))
    fetchAPI<SchedulerStatus>("/scheduler")
      .then(s => {
        setScheduler(s)
        setSyncEnabled(s.sync_enabled); setSyncInterval(s.sync_interval_minutes)
        setQqEnabled(s.qq_enabled); setQqInterval(s.qq_interval_minutes)
        setTgEnabled(s.telegram_enabled); setTgInterval(s.telegram_interval_minutes)
        setAnalyzeEnabled(s.analyze_enabled); setAnalyzeInterval(s.analyze_interval_minutes)
        setTagEnabled(s.tag_enabled); setTagInterval(s.tag_interval_minutes)
        setTodoRemindEnabled(s.todo_remind_enabled); setTodoRemindInterval(s.todo_remind_interval_minutes)
        setReportWeeklyEnabled(s.report_weekly_enabled); setReportMonthlyEnabled(s.report_monthly_enabled)
      })
      // Without this, a failed load silently leaves every toggle at its OFF
      // default — and saving would then disable all scheduled jobs unnoticed.
      .catch(e => toast.error(`定时配置加载失败：${getErrorMessage(e)}`))
  }, [])

  const onTabChange = (v: string) => {
    setTab(v as TabKey)
    setSearchParams(prev => {
      const p = new URLSearchParams(prev)
      p.set("tab", v)
      return p
    })
  }

  const saveGeneral = async () => {
    if (!config) return
    setSaving(true)
    try {
      const updated = await fetchAPI<Config>("/config", {
        method: "PUT",
        body: JSON.stringify({
          daily_token_budget: Number(budget),
          budget_action: action,
          vip_contacts: vip.split(",").map(s => s.trim()).filter(Boolean),
          self_names: selfNames.split(/[,，]/).map(s => s.trim()).filter(Boolean),
          wechat_account: wechatAccount,
          keywords: keywords.split(",").map(s => s.trim()).filter(Boolean),
          api_token: apiToken,
          semantic_triggers: triggers.filter(t => t.query.trim()),
          llm_provider: llmProvider,
          llm_api_url: llmUrl,
          llm_model: llmModel,
          llm_api_key: llmApiKey === MASKED ? undefined : llmApiKey,
          llm_embedding_model: llmEmbeddingModel,
        }),
      })
      if (apiToken !== MASKED) {
        try {
          if (apiToken) localStorage.setItem("apiToken", apiToken)
          else localStorage.removeItem("apiToken")
        } catch { /* private mode */ }
      }
      setConfig(updated)
      setApiToken(updated.api_token || "")
      toast.success("已保存")
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  const saveScheduler = async () => {
    setSavingScheduler(true)
    try {
      const updated = await fetchAPI<SchedulerStatus>("/scheduler", {
        method: "PUT",
        body: JSON.stringify({
          sync_enabled: syncEnabled, sync_interval_minutes: syncInterval,
          qq_enabled: qqEnabled, qq_interval_minutes: qqInterval,
          telegram_enabled: tgEnabled, telegram_interval_minutes: tgInterval,
          analyze_enabled: analyzeEnabled, analyze_interval_minutes: analyzeInterval,
          tag_enabled: tagEnabled, tag_interval_minutes: tagInterval,
          todo_remind_enabled: todoRemindEnabled, todo_remind_interval_minutes: todoRemindInterval,
          report_weekly_enabled: reportWeeklyEnabled, report_monthly_enabled: reportMonthlyEnabled,
        }),
      })
      setScheduler(updated)
      toast.success("已保存定时配置")
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setSavingScheduler(false)
    }
  }

  if (!config && !error) return <div className="p-8"><p className="text-[var(--color-muted-foreground)] text-sm">加载中...</p></div>
  if (!config) return <div className="p-8"><p className="text-[var(--color-destructive)] text-sm">{error}</p></div>

  return (
    <div className="p-6 md:p-8 max-w-4xl mx-auto">
      <header className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight">设置</h1>
      </header>

      <Tabs value={tab} onValueChange={onTabChange}>
        <TabsList className="flex-wrap">
          <TabsTrigger value="general">常规</TabsTrigger>
          <TabsTrigger value="model">AI 模型</TabsTrigger>
          <TabsTrigger value="sources">数据源</TabsTrigger>
          <TabsTrigger value="scheduler">定时任务</TabsTrigger>
          <TabsTrigger value="backups">备份</TabsTrigger>
        </TabsList>

        <TabsContent value="general">
          <section className="max-w-xl space-y-4">
            <div className="space-y-1.5">
              <label className="text-sm text-[var(--color-muted-foreground)]">每日 Token 预算</label>
              <Input type="number" value={budget} onChange={e => setBudget(e.target.value)} className="max-w-xs" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm text-[var(--color-muted-foreground)]">预算策略</label>
              <Select value={action} onChange={e => setAction(e.target.value)} className="max-w-xs">
                <option value="stop">停止</option>
                <option value="warn">警告</option>
                <option value="skip">跳过</option>
                <option value="pause_and_notify">暂停并通知</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm text-[var(--color-muted-foreground)]">VIP 联系人（逗号分隔）</label>
              <Input value={vip} onChange={e => setVip(e.target.value)} className="max-w-sm" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm text-[var(--color-muted-foreground)]">
                我的昵称（逗号分隔）—— 「@消息」页据此找出 @我 的消息；留空则自动推断
              </label>
              <Input value={selfNames} onChange={e => setSelfNames(e.target.value)} className="max-w-sm" placeholder="子非鱼, yu yan" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm text-[var(--color-muted-foreground)]">
                微信账号标签 —— 有多个微信号时给当前登录的号起个名（如「主号」），新同步的消息会带上它，统计页可按号切换
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <Input value={wechatAccount} onChange={e => setWechatAccount(e.target.value)} className="max-w-xs" placeholder="主号" />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={assigning || !wechatAccount.trim()}
                  title="把此前没有标签的微信历史消息归入这个标签，并重建统计。改写期间（微信十万条约几秒）同步会短暂等待。"
                  onClick={async () => {
                    if (!confirm(`把所有未标记账号的微信历史消息归入「${wechatAccount.trim()}」？\n\n这会改写历史消息的账号字段并全量重建统计（约半分钟），期间正在进行的同步会短暂等待。`)) return
                    setAssigning(true)
                    try {
                      const r = await fetchAPI<{ updated: number }>("/stats/accounts/assign", {
                        method: "POST",
                        body: JSON.stringify({ platform: "wechat", from_account: "", to_account: wechatAccount.trim() }),
                      })
                      toast.success(`已归入 ${r.updated.toLocaleString()} 条消息，统计重建中`)
                    } catch (e) {
                      toast.error(getErrorMessage(e))
                    } finally {
                      setAssigning(false)
                    }
                  }}
                >
                  {assigning && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}历史消息归入此标签
                </Button>
              </div>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm text-[var(--color-muted-foreground)]">
                关键词（逗号分隔）—— 命中后会进收件箱、可选系统通知
              </label>
              <Input value={keywords} onChange={e => setKeywords(e.target.value)} className="max-w-sm" placeholder="项目, deadline, 紧急" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm text-[var(--color-muted-foreground)]">
                语义触发器 —— 消息与描述语义相近时进收件箱（需 Embedding 模型）
              </label>
              <div className="space-y-2">
                {triggers.map((t, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input
                      value={t.query}
                      onChange={e => setTriggers(prev => prev.map((x, j) => j === i ? { ...x, query: e.target.value } : x))}
                      placeholder="如：有人在讨论服务器故障"
                      className="max-w-sm"
                    />
                    <Input
                      type="number"
                      min={0}
                      max={1}
                      step={0.05}
                      value={t.threshold}
                      onChange={e => setTriggers(prev => prev.map((x, j) => j === i ? { ...x, threshold: Number(e.target.value) } : x))}
                      className="w-24"
                    />
                    <button
                      onClick={() => setTriggers(prev => prev.filter((_, j) => j !== i))}
                      aria-label="删除触发器"
                      className="rounded p-1.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-destructive)]/10 hover:text-[var(--color-destructive)]"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
                <Button variant="outline" size="sm" onClick={() => setTriggers(prev => [...prev, { query: "", threshold: 0.6 }])}>
                  <Plus className="mr-1 h-3.5 w-3.5" />添加触发器
                </Button>
              </div>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm text-[var(--color-muted-foreground)]">
                API Token —— 设置后所有 API 请求需携带此令牌（留空则关闭鉴权）
              </label>
              <Input value={apiToken} onChange={e => setApiToken(e.target.value)} className="max-w-sm" placeholder="局域网暴露时建议设置" />
            </div>
            <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
              筛选模式：<Badge variant="secondary">{config.chat_filter.mode}</Badge>
              <span>({config.chat_filter.chats.length} 个聊天)</span>
              <Link to="/chats" className="text-xs text-[var(--color-info)] hover:underline ml-auto">
                在「聊天」页编辑 →
              </Link>
            </div>
            <Button onClick={saveGeneral} disabled={saving}>
              {saving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}保存
            </Button>
          </section>
        </TabsContent>

        <TabsContent value="model">
          <LLMTab
            provider={llmProvider} url={llmUrl} model={llmModel} apiKey={llmApiKey} embeddingModel={llmEmbeddingModel}
            setProvider={setLlmProvider} setUrl={setLlmUrl} setModel={setLlmModel} setApiKey={setLlmApiKey} setEmbeddingModel={setLlmEmbeddingModel}
            onError={setError}
          />
          <Button className="mt-4" onClick={saveGeneral} disabled={saving}>
            {saving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}保存模型配置
          </Button>
        </TabsContent>

        <TabsContent value="sources">
          <div className="space-y-6">
            <SyncScopeCard config={config} onConfigChange={setConfig} />
            <ImportTab config={config} onConfigChange={setConfig} />
          </div>
        </TabsContent>

        <TabsContent value="scheduler">
          <section className="max-w-xl space-y-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold"><Clock className="h-4 w-4" />定时任务</h2>

            <SchedulerRow
              title="自动同步微信" enabled={syncEnabled} setEnabled={setSyncEnabled}
              interval={syncInterval} setInterval={setSyncInterval}
              lastAt={scheduler?.last_sync_at} nextAt={scheduler?.next_sync_at}
            />
            <SchedulerRow
              title="自动同步 QQ" enabled={qqEnabled} setEnabled={setQqEnabled}
              interval={qqInterval} setInterval={setQqInterval}
              lastAt={scheduler?.last_qq_sync_at} nextAt={scheduler?.next_qq_sync_at}
              helper={!config.qq.enabled ? "提示：QQ 未启用，定时任务不会运行" : undefined}
            />
            <SchedulerRow
              title="自动同步 Telegram" enabled={tgEnabled} setEnabled={setTgEnabled}
              interval={tgInterval} setInterval={setTgInterval}
              lastAt={scheduler?.last_telegram_sync_at} nextAt={scheduler?.next_telegram_sync_at}
              helper={!config.telegram.enabled ? "提示：Telegram 未登录，定时任务不会运行" : undefined}
            />
            <SchedulerRow
              title="自动分析" enabled={analyzeEnabled} setEnabled={setAnalyzeEnabled}
              interval={analyzeInterval} setInterval={setAnalyzeInterval}
              minInterval={10}
              lastAt={scheduler?.last_analyze_at} nextAt={scheduler?.next_analyze_at}
            />
            <SchedulerRow
              title="自动打标签" enabled={tagEnabled} setEnabled={setTagEnabled}
              interval={tagInterval} setInterval={setTagInterval}
              minInterval={30}
              lastAt={scheduler?.last_tag_at} nextAt={scheduler?.next_tag_at}
              helper="AI 给未打标签的私聊好友自动生成标签建议，结果在「标签 → 待审核」中确认"
            />
            <SchedulerRow
              title="待办提醒" enabled={todoRemindEnabled} setEnabled={setTodoRemindEnabled}
              interval={todoRemindInterval} setInterval={setTodoRemindInterval}
              minInterval={30}
              lastAt={scheduler?.last_todo_remind_at} nextAt={scheduler?.next_todo_remind_at}
              helper="定期通过系统通知提醒未完成的高紧急度待办"
            />
            <ReportRow
              title="自动生成周报" enabled={reportWeeklyEnabled} setEnabled={setReportWeeklyEnabled}
              lastAt={scheduler?.last_report_weekly_at} nextAt={scheduler?.next_report_weekly_at}
              helper="每 7 天生成一份周报（消息动态、新增知识点、待办、VIP 动态），在「报告」页查看"
            />
            <ReportRow
              title="自动生成月报" enabled={reportMonthlyEnabled} setEnabled={setReportMonthlyEnabled}
              lastAt={scheduler?.last_report_monthly_at} nextAt={scheduler?.next_report_monthly_at}
              helper="每 30 天生成一份月报"
            />

            <Button size="sm" disabled={savingScheduler || !scheduler} onClick={saveScheduler}>
              {savingScheduler && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}保存定时配置
            </Button>
            {!scheduler && <p className="text-xs text-[var(--color-destructive)]">定时配置未加载，暂不能保存（避免误关全部任务）</p>}
          </section>
        </TabsContent>

        <TabsContent value="backups">
          <div className="space-y-6">
            <BackupsPanel />
            <StoragePanel onConfigChange={setConfig} />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function ReportRow({ title, enabled, setEnabled, lastAt, nextAt, helper }: {
  title: string
  enabled: boolean
  setEnabled: (v: boolean) => void
  lastAt?: string | null
  nextAt?: string | null
  helper?: string
}) {
  return (
    <div className="space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-4">
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium">{title}</label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} className="accent-[var(--color-primary)]" />
          {enabled ? "已开启" : "已关闭"}
        </label>
      </div>
      {lastAt && <p className="text-xs text-[var(--color-muted-foreground)]">上次: {formatTime(lastAt)}</p>}
      {nextAt && enabled && <p className="text-xs text-[var(--color-muted-foreground)]">下次: {formatTime(nextAt)}</p>}
      {helper && <p className="text-xs text-[var(--color-muted-foreground)]">{helper}</p>}
    </div>
  )
}

function SchedulerRow({
  title, enabled, setEnabled, interval, setInterval: setInt, minInterval = 5,
  lastAt, nextAt, helper,
}: {
  title: string
  enabled: boolean
  setEnabled: (v: boolean) => void
  interval: number
  setInterval: (v: number) => void
  minInterval?: number
  lastAt?: string | null
  nextAt?: string | null
  helper?: string
}) {
  return (
    <div className="space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-4">
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium">{title}</label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} className="accent-[var(--color-primary)]" />
          {enabled ? "已开启" : "已关闭"}
        </label>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm text-[var(--color-muted-foreground)]">间隔</span>
        <Input type="number" min={minInterval} value={interval} onChange={e => setInt(Number(e.target.value))} className="w-20" />
        <span className="text-sm text-[var(--color-muted-foreground)]">分钟</span>
      </div>
      {lastAt && <p className="text-xs text-[var(--color-muted-foreground)]">上次: {formatTime(lastAt)}</p>}
      {nextAt && enabled && <p className="text-xs text-[var(--color-muted-foreground)]">下次: {formatTime(nextAt)}</p>}
      {helper && <p className="text-xs text-[var(--color-warning)]">{helper}</p>}
    </div>
  )
}
