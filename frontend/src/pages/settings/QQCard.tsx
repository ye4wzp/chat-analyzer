import { useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, subscribeSSE, type Config, type QQTestResponse, type TaskProgress } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Loader2, CheckCircle2, AlertCircle, Eye, EyeOff, ChevronDown, ChevronRight } from "lucide-react"
import { QQLauncherPanel } from "./QQLauncherPanel"

interface Props {
  config: Config
  onConfigChange: (cfg: Config) => void
}

export function QQCard({ config, onConfigChange }: Props) {
  const [host, setHost] = useState(config.qq.host)
  const [port, setPort] = useState(String(config.qq.port))
  const [token, setToken] = useState(config.qq.token)
  const [showToken, setShowToken] = useState(false)
  const [testStatus, setTestStatus] = useState<"idle" | "loading" | "ok" | "fail">("idle")
  const [testResult, setTestResult] = useState<QQTestResponse | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const isMasked = token === "********"
  const enabled = config.qq.enabled

  const save = async (overrides: Partial<{ qq_enabled: boolean }> = {}) => {
    setSaving(true)
    try {
      const updated = await fetchAPI<Config>("/config", {
        method: "PUT",
        body: JSON.stringify({
          qq_host: host,
          qq_port: Number(port),
          qq_token: isMasked ? undefined : token,
          ...overrides,
        }),
      })
      onConfigChange(updated)
      toast.success("已保存")
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTestStatus("loading")
    setTestResult(null)
    try {
      // Save first so backend uses fresh token
      await save()
      const res = await fetchAPI<QQTestResponse>("/qq/test", { method: "POST" })
      setTestStatus("ok")
      setTestResult(res)
    } catch (e) {
      setTestStatus("fail")
      toast.error(getErrorMessage(e))
    }
  }

  const sync = async () => {
    if (syncing) return
    setSyncing(true)
    const id = toast.loading("QQ 同步启动中...")
    try {
      const { task_id } = await fetchAPI<{ task_id: string }>("/sync/qq", { method: "POST" })
      const es = subscribeSSE<TaskProgress>(`/tasks/${task_id}/events`, (data) => {
        toast.loading(data.message || data.status, { id })
        if (data.status === "done") {
          es.close()
          toast.success(data.message || "同步完成", { id })
          setSyncing(false)
        } else if (data.status === "error") {
          es.close()
          toast.error(data.message || "同步失败", { id })
          setSyncing(false)
        }
      }, () => { toast.dismiss(id); setSyncing(false) })
    } catch (e) {
      toast.error(getErrorMessage(e), { id })
      setSyncing(false)
    }
  }

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <span className="h-2 w-2 rounded-full" style={{ background: "var(--color-platform-qq)" }} />
            QQ
          </h3>
          <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
            通过 NapCat-QCE 自动拉取消息
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded-full ${enabled ? "bg-[var(--color-success)]/15 text-[var(--color-success)]" : "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]"}`}>
            {enabled ? "已启用" : "未启用"}
          </span>
        </div>
      </div>

      <div className="mb-4">
        <QQLauncherPanel onConfigChange={onConfigChange} />
      </div>

      <button
        type="button"
        onClick={() => setShowAdvanced(v => !v)}
        className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] mb-2"
      >
        {showAdvanced ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        手动配置（高级 / 连接外部 QCE）
      </button>

      {showAdvanced && (
      <div className="space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2 space-y-1">
            <label className="text-xs text-[var(--color-muted-foreground)]">Host</label>
            <Input value={host} onChange={e => setHost(e.target.value)} placeholder="127.0.0.1" />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-[var(--color-muted-foreground)]">端口</label>
            <Input value={port} onChange={e => setPort(e.target.value)} placeholder="40653" />
          </div>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-[var(--color-muted-foreground)]">Access Token</label>
          <div className="relative">
            <Input
              type={showToken || isMasked ? "text" : "password"}
              value={token}
              onChange={e => setToken(e.target.value)}
              placeholder="从 QCE 控制台复制"
              className="pr-9"
            />
            <button
              type="button"
              onClick={() => setShowToken(v => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
              aria-label="显示 Token"
            >
              {showToken ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>
      </div>
      )}

      <div className="space-y-3 mt-3">
        {testResult?.ok && (
          <div className="flex items-center gap-2 rounded-md bg-[var(--color-success)]/10 px-3 py-2 text-xs text-[var(--color-success)]">
            <CheckCircle2 className="h-3.5 w-3.5" />
            已连接 — {testResult.nick} ({testResult.uin})
          </div>
        )}
        {testStatus === "fail" && !testResult && (
          <div className="flex items-center gap-2 rounded-md bg-[var(--color-destructive)]/10 px-3 py-2 text-xs text-[var(--color-destructive)]">
            <AlertCircle className="h-3.5 w-3.5" />连接失败，请检查 QCE 是否运行
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-2">
          <Button variant="outline" size="sm" onClick={test} disabled={testStatus === "loading" || saving}>
            {testStatus === "loading" && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            测试连接
          </Button>
          {showAdvanced && (
            <Button variant="outline" size="sm" onClick={() => save()} disabled={saving}>
              {saving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}保存
            </Button>
          )}
          <Button
            variant={enabled ? "secondary" : "default"}
            size="sm"
            onClick={() => save({ qq_enabled: !enabled })}
            disabled={saving || (!token && !config.qq.token)}
          >
            {enabled ? "停用" : "启用"}
          </Button>
          <div className="ml-auto">
            <Button size="sm" onClick={sync} disabled={syncing || !enabled}>
              {syncing && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}立即同步
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
