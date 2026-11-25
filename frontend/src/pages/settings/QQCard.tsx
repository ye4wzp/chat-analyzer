import { useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, type Config, type QQTestResponse } from "@/lib/api"
import { useTaskRunner } from "@/hooks/useTaskRunner"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Loader2, CheckCircle2, AlertCircle, Eye, EyeOff, ChevronDown, ChevronRight } from "lucide-react"
import { QQLauncherPanel } from "./QQLauncherPanel"
import { MASKED } from "@/lib/constants"

interface Props {
  config: Config
  onConfigChange: (cfg: Config) => void
}

export function QQCard({ config, onConfigChange }: Props) {
  const [source, setSource] = useState(config.qq.source)
  const [path, setPath] = useState(config.qq.local_path)
  const [account, setAccount] = useState(config.qq.local_account)
  const [failure, setFailure] = useState("")
  const local = source === "local"
  const [host, setHost] = useState(config.qq.host)
  const [port, setPort] = useState(String(config.qq.port))
  const [token, setToken] = useState(config.qq.token)
  const [showToken, setShowToken] = useState(false)
  const [testStatus, setTestStatus] = useState<"idle" | "loading" | "ok" | "fail">("idle")
  const [testResult, setTestResult] = useState<QQTestResponse | null>(null)
  const [saving, setSaving] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const { run, running } = useTaskRunner()
  const syncing = running.has("sync_qq")

  const isMasked = token === MASKED
  const enabled = config.qq.enabled

  const save = async (overrides: Partial<{ qq_enabled: boolean }> = {}) => {
    setSaving(true)
    try {
      const updated = await fetchAPI<Config>("/config", {
        method: "PUT",
        body: JSON.stringify({
          qq_source: source,
          qq_local_path: path,
          qq_local_account: account,
          ...(!local && { qq_host: host, qq_port: Number(port), qq_token: isMasked ? undefined : token }),
          ...overrides,
        }),
      })
      setPath(updated.qq.local_path)
      setAccount(updated.qq.local_account)
      setToken(updated.qq.token)
      onConfigChange(updated)
      return true
    } catch (e) {
      toast.error(getErrorMessage(e))
      return false
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTestStatus("loading")
    setTestResult(null)
    try {
      // Save first so backend uses fresh token
      if (!await save()) { setTestStatus("idle"); return }
      const res = await fetchAPI<QQTestResponse>("/qq/test", { method: "POST" })
      setTestStatus("ok")
      setTestResult(res)
    } catch (e) {
      setFailure(getErrorMessage(e))
      setTestStatus("fail")
      toast.error(getErrorMessage(e))
    }
  }

  const dirty = source !== config.qq.source || path !== config.qq.local_path || account !== config.qq.local_account ||
    (!local && (host !== config.qq.host || Number(port) !== config.qq.port || token !== config.qq.token))
  const sync = (mode: "incremental" | "repair" = "incremental") => run({
    start: () => fetchAPI<{ task_id: string }>("/sync/qq", { method: "POST", body: JSON.stringify({ mode }) }),
    taskType: "sync_qq",
    loadingText: "QQ 同步启动中...",
  })

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <span className="h-2 w-2 rounded-full" style={{ background: "var(--color-platform-qq)" }} />
            QQ
          </h3>
          <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
            {local ? "读取本机 QQ 消息数据库，支持私聊和群聊" : "通过 NapCat-QCE 自动拉取消息"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded-full ${enabled ? "bg-[var(--color-success)]/15 text-[var(--color-success)]" : "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]"}`}>
            {enabled ? "已启用" : "未启用"}
          </span>
        </div>
      </div>

      <label className="mb-4 flex items-center gap-3 text-sm">消息来源
        <select className="rounded border px-2 py-1" value={source} onChange={e => {
          setSource(e.target.value as typeof source); setTestStatus("idle"); setTestResult(null)
        }}>
          <option value="qce">NapCat-QCE</option>
          <option value="local">本地数据库（qq-cli）</option>
        </select>
      </label>
      {local ? <div className="space-y-3">
        <label className="block text-sm">数据库所属的 QQ 账号
          <Input value={account} onChange={e => setAccount(e.target.value)} placeholder="用于区分各账号的消息和同步进度" />
        </label>
        <label className="block text-sm">可读的消息库文件或目录
          <Input value={path} onChange={e => setPath(e.target.value)} placeholder="默认 ~/.qqmac/plain/nt_msg.db" />
        </label>
        <p className="text-xs text-[var(--color-muted-foreground)]">首次读取已有历史。之后同步读取副本中的新增记录，并回查最近一天。更新副本后，可用“补查全部历史”找回更早的消息。</p>
      </div> : <>
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

      </>}
      <div className="space-y-3 mt-3">
        {!dirty && testResult?.ok && (
          <div className="flex items-center gap-2 rounded-md bg-[var(--color-success)]/10 px-3 py-2 text-xs text-[var(--color-success)]">
            <CheckCircle2 className="h-3.5 w-3.5" />
            已连接 — {testResult.nick} ({testResult.uin})
          </div>
        )}
        {!dirty && testStatus === "fail" && !testResult && (
          <div className="flex items-center gap-2 rounded-md bg-[var(--color-destructive)]/10 px-3 py-2 text-xs text-[var(--color-destructive)]">
            <AlertCircle className="h-3.5 w-3.5" />{failure}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-2">
          <Button variant="outline" size="sm" onClick={test} disabled={testStatus === "loading" || saving}>
            {testStatus === "loading" && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            测试连接
          </Button>
          {(local || showAdvanced || dirty) && (
            <Button variant="outline" size="sm" onClick={async () => { if (await save()) toast.success("已保存") }} disabled={saving}>
              {saving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}保存
            </Button>
          )}
          <Button
            variant={enabled ? "secondary" : "default"}
            size="sm"
            onClick={() => save({ qq_enabled: !enabled })}
            disabled={saving || (!enabled && (local ? !account.trim() : !token))}
          >
            {enabled ? "停用" : "启用"}
          </Button>
          {local && <Button variant="outline" size="sm" onClick={() => void sync("repair")}
            disabled={syncing || saving || dirty || !enabled}>补查全部历史</Button>}
          <div className="ml-auto">
            <Button size="sm" onClick={() => void sync()} disabled={syncing || saving || dirty || !enabled}>
              {syncing && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}立即同步
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
