import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import {
  fetchAPI,
  getErrorMessage,
  subscribeSSE,
  type Config,
  type QQLauncherStatus,
  type TaskProgress,
} from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Loader2, CheckCircle2, AlertCircle, ExternalLink,
  Play, Square, Download, FileText, RefreshCw,
} from "lucide-react"

interface Props {
  onConfigChange: (cfg: Config) => void
}

export function QQLauncherPanel({ onConfigChange }: Props) {
  const [status, setStatus] = useState<QQLauncherStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<"install" | "start" | "stop" | null>(null)
  const [logs, setLogs] = useState<string>("")
  const [showLogs, setShowLogs] = useState(false)
  const pollRef = useRef<number | null>(null)

  const refresh = async () => {
    try {
      const s = await fetchAPI<QQLauncherStatus>("/qq/launcher/status")
      setStatus(s)
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
    pollRef.current = window.setInterval(refresh, 4000)
    return () => { if (pollRef.current) window.clearInterval(pollRef.current) }
  }, [])

  const runInstall = async () => {
    if (busy) return
    setBusy("install")
    const id = toast.loading("QCE 安装启动中...")
    try {
      const { task_id } = await fetchAPI<{ task_id: string }>(
        "/qq/launcher/install", { method: "POST" }
      )
      const es = subscribeSSE<TaskProgress>(`/tasks/${task_id}/events`, (data) => {
        toast.loading(data.message || data.status, { id })
        if (data.status === "done") {
          es.close()
          toast.success(data.message || "安装完成", { id })
          setBusy(null); void refresh()
        } else if (data.status === "error") {
          es.close()
          toast.error(data.message || "安装失败", { id })
          setBusy(null)
        }
      }, () => { toast.dismiss(id); setBusy(null) })
    } catch (e) {
      toast.error(getErrorMessage(e), { id })
      setBusy(null)
    }
  }

  const runStart = async () => {
    if (busy) return
    setBusy("start")
    try {
      const s = await fetchAPI<QQLauncherStatus>("/qq/launcher/start", { method: "POST" })
      setStatus(s)
      toast.success("已启动，请在 WebUI 扫码登录")
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  const runStop = async () => {
    if (busy) return
    setBusy("stop")
    try {
      const s = await fetchAPI<QQLauncherStatus>("/qq/launcher/stop", { method: "POST" })
      setStatus(s)
      toast.success("已停止")
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  const loadLogs = async () => {
    try {
      const { logs } = await fetchAPI<{ logs: string }>("/qq/launcher/logs?tail=200")
      setLogs(logs)
      setShowLogs(true)
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  if (loading) {
    return (
      <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-4 text-xs text-[var(--color-muted-foreground)]">
        <Loader2 className="inline h-3.5 w-3.5 animate-spin mr-1.5" />检查 Docker 环境...
      </div>
    )
  }

  if (!status) return null

  const docker = status.docker
  const containerRunning = status.container === "running"
  const containerExists = status.container !== null

  // Status badge
  let badge = <Badge variant="idle">未就绪</Badge>
  if (!docker.daemon) badge = <Badge variant="error">Docker 未运行</Badge>
  else if (!status.installed) badge = <Badge variant="warn">待安装</Badge>
  else if (!containerExists) badge = <Badge variant="idle">未启动</Badge>
  else if (containerRunning && status.token_captured) badge = <Badge variant="ok">运行中</Badge>
  else if (containerRunning) badge = <Badge variant="warn">等待扫码</Badge>
  else badge = <Badge variant="idle">已停止</Badge>

  return (
    <div className="space-y-3">
      {/* Status header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
          <span>Docker 托管</span>
          {badge}
          {status.installed_version && (
            <span className="font-mono">{status.installed_version}</span>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={refresh} disabled={!!busy}>
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Docker check */}
      {!docker.installed && (
        <Alert kind="error" icon={<AlertCircle className="h-3.5 w-3.5" />}>
          未检测到 docker 命令，请先安装{" "}
          <a href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noreferrer"
             className="underline inline-flex items-center gap-0.5">
            Docker Desktop<ExternalLink className="h-3 w-3" />
          </a>
        </Alert>
      )}
      {docker.installed && !docker.daemon && (
        <Alert kind="error" icon={<AlertCircle className="h-3.5 w-3.5" />}>
          Docker Daemon 未运行，请打开 Docker Desktop 应用
        </Alert>
      )}

      {/* Action buttons */}
      {docker.daemon && (
        <div className="flex flex-wrap items-center gap-2">
          {!status.installed && (
            <Button size="sm" onClick={runInstall} disabled={!!busy}>
              {busy === "install" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                : <Download className="mr-1.5 h-3.5 w-3.5" />}
              安装最新 QCE
            </Button>
          )}
          {status.installed && !containerRunning && (
            <Button size="sm" onClick={runStart} disabled={!!busy}>
              {busy === "start" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                : <Play className="mr-1.5 h-3.5 w-3.5" />}
              启动
            </Button>
          )}
          {status.installed && (
            <Button variant="outline" size="sm" onClick={runInstall} disabled={!!busy}>
              {busy === "install" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />}
              更新
            </Button>
          )}
          {containerExists && (
            <Button variant="outline" size="sm" onClick={runStop} disabled={!!busy}>
              {busy === "stop" ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                : <Square className="mr-1.5 h-3.5 w-3.5" />}
              停止
            </Button>
          )}
          {containerExists && (
            <Button variant="ghost" size="sm" onClick={loadLogs}>
              <FileText className="mr-1.5 h-3.5 w-3.5" />日志
            </Button>
          )}
        </div>
      )}

      {/* Running state: WebUI link + token status */}
      {containerRunning && (
        <div className="rounded-md bg-[var(--color-info)]/5 border border-[var(--color-info)]/20 p-3 text-xs space-y-2">
          <div className="flex items-center gap-2">
            <span className="font-medium">下一步：</span>
            {!status.token_captured ? (
              <>
                打开
                <a href={status.webui_url} target="_blank" rel="noreferrer"
                   className="text-[var(--color-info)] underline inline-flex items-center gap-0.5">
                  NapCat WebUI<ExternalLink className="h-3 w-3" />
                </a>
                扫码登录 QQ
              </>
            ) : (
              <>
                <CheckCircle2 className="h-3.5 w-3.5 text-[var(--color-success)]" />
                <span className="text-[var(--color-success)]">Token 已自动写入，可直接测试连接</span>
              </>
            )}
          </div>
        </div>
      )}

      {/* Log viewer */}
      {showLogs && (
        <div className="rounded-md border border-[var(--color-border)] bg-black/90 text-green-300 p-3 relative">
          <button
            onClick={() => setShowLogs(false)}
            className="absolute top-1 right-2 text-xs text-gray-400 hover:text-white"
          >关闭</button>
          <pre className="text-[10px] leading-relaxed font-mono whitespace-pre-wrap max-h-72 overflow-auto">
            {logs || "(无日志)"}
          </pre>
        </div>
      )}
    </div>
  )
  // onConfigChange kept in props for parity; token refresh is driven by parent polling.
  // We consume it to avoid lint noise if caller expects it.
  void onConfigChange
}

// Lightweight inline components kept local to this panel.

function Badge({ children, variant }: {
  children: React.ReactNode
  variant: "ok" | "warn" | "error" | "idle"
}) {
  const styles = {
    ok: "bg-[var(--color-success)]/15 text-[var(--color-success)]",
    warn: "bg-[var(--color-warning)]/15 text-[var(--color-warning)]",
    error: "bg-[var(--color-destructive)]/15 text-[var(--color-destructive)]",
    idle: "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]",
  }[variant]
  return <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${styles}`}>{children}</span>
}

function Alert({ children, kind, icon }: {
  children: React.ReactNode
  kind: "info" | "error" | "warn"
  icon?: React.ReactNode
}) {
  const bg = {
    info: "bg-[var(--color-info)]/5 border-[var(--color-info)]/20 text-[var(--color-muted-foreground)]",
    warn: "bg-[var(--color-warning)]/5 border-[var(--color-warning)]/20 text-[var(--color-warning)]",
    error: "bg-[var(--color-destructive)]/5 border-[var(--color-destructive)]/20 text-[var(--color-destructive)]",
  }[kind]
  return (
    <div className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs ${bg}`}>
      {icon}
      <div className="flex-1">{children}</div>
    </div>
  )
}
