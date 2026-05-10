import { useEffect, useState } from "react"
import { toast } from "sonner"
import {
  fetchAPI,
  getErrorMessage,
  subscribeSSE,
  type TaskProgress,
  type TelegramStatus,
  type TelegramLoginConfirmResponse,
} from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Loader2, ExternalLink, LogOut, RefreshCw, ArrowRight } from "lucide-react"

type Step = "idle" | "credentials" | "code" | "password" | "done"

export function TelegramCard() {
  const [status, setStatus] = useState<TelegramStatus | null>(null)
  const [step, setStep] = useState<Step>("idle")
  const [apiId, setApiId] = useState("")
  const [apiHash, setApiHash] = useState("")
  const [phone, setPhone] = useState("")
  const [code, setCode] = useState("")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [syncing, setSyncing] = useState(false)

  useEffect(() => { void refresh() }, [])

  const refresh = async () => {
    try {
      const s = await fetchAPI<TelegramStatus>("/telegram/status")
      setStatus(s)
      setStep(s.logged_in ? "done" : "idle")
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  const startLogin = async () => {
    setBusy(true)
    try {
      await fetchAPI<{ phone_code_hash: string }>("/telegram/login/start", {
        method: "POST",
        body: JSON.stringify({ api_id: Number(apiId), api_hash: apiHash, phone }),
      })
      toast.success("验证码已发送，请到 Telegram 客户端查看")
      setStep("code")
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (withPassword?: string) => {
    setBusy(true)
    try {
      const res = await fetchAPI<TelegramLoginConfirmResponse>("/telegram/login/confirm", {
        method: "POST",
        body: JSON.stringify({ phone, code, password: withPassword }),
      })
      if (res.need_password) {
        setStep("password")
        toast.message("此账号开启了两步验证，请输入密码")
        return
      }
      toast.success(`已登录 ${res.username || res.first_name || ""}`)
      await refresh()
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const logout = async () => {
    setBusy(true)
    try {
      await fetchAPI("/telegram/logout", { method: "POST" })
      toast.success("已退出登录")
      setApiId(""); setApiHash(""); setPhone(""); setCode(""); setPassword("")
      await refresh()
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const sync = async () => {
    if (syncing) return
    setSyncing(true)
    const id = toast.loading("Telegram 同步启动中...")
    try {
      const { task_id } = await fetchAPI<{ task_id: string }>("/sync/telegram", { method: "POST" })
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

  const loggedIn = status?.logged_in === true

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <span className="h-2 w-2 rounded-full" style={{ background: "var(--color-platform-telegram)" }} />
            Telegram
          </h3>
          <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
            用账号 API 一键登录，自动拉取所有对话历史
          </p>
        </div>
        <span className={`text-xs px-2 py-0.5 rounded-full ${loggedIn ? "bg-[var(--color-success)]/15 text-[var(--color-success)]" : "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]"}`}>
          {loggedIn ? `已登录 @${status?.username || status?.first_name || ""}` : "未登录"}
        </span>
      </div>

      {!loggedIn && step === "idle" && (
        <div className="space-y-3">
          <div className="rounded-md border border-[var(--color-info)]/20 bg-[var(--color-info)]/5 p-3 text-xs text-[var(--color-muted-foreground)] leading-relaxed">
            <p className="font-medium text-[var(--color-info)] mb-1.5">前置准备（一次性）</p>
            <ol className="space-y-0.5 list-decimal list-inside">
              <li>访问 <a href="https://my.telegram.org/auth" target="_blank" rel="noreferrer" className="text-[var(--color-info)] underline inline-flex items-center gap-0.5">my.telegram.org<ExternalLink className="h-3 w-3" /></a> 用手机号登录</li>
              <li>进入 "API development tools" 创建 application</li>
              <li>记下 <code className="font-mono text-[var(--color-foreground)]">api_id</code> 和 <code className="font-mono text-[var(--color-foreground)]">api_hash</code></li>
            </ol>
          </div>
          <Button onClick={() => setStep("credentials")} className="w-full">开始登录</Button>
        </div>
      )}

      {step === "credentials" && (
        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs text-[var(--color-muted-foreground)]">API ID</label>
            <Input value={apiId} onChange={e => setApiId(e.target.value)} placeholder="1234567" />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-[var(--color-muted-foreground)]">API Hash</label>
            <Input value={apiHash} onChange={e => setApiHash(e.target.value)} placeholder="abcdef0123456789..." />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-[var(--color-muted-foreground)]">手机号（含国家码）</label>
            <Input value={phone} onChange={e => setPhone(e.target.value)} placeholder="+8613800138000" />
          </div>
          <div className="flex gap-2 pt-1">
            <Button variant="outline" size="sm" onClick={() => setStep("idle")} disabled={busy}>返回</Button>
            <Button onClick={startLogin} disabled={busy || !apiId || !apiHash || !phone} className="flex-1">
              {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              发送验证码 <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}

      {step === "code" && (
        <div className="space-y-3">
          <p className="text-xs text-[var(--color-muted-foreground)]">
            请在 Telegram 客户端查看验证码（可能在 "Telegram" 官方账号会话或短信中）
          </p>
          <div className="space-y-1">
            <label className="text-xs text-[var(--color-muted-foreground)]">验证码</label>
            <Input value={code} onChange={e => setCode(e.target.value)} placeholder="12345" />
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setStep("credentials")} disabled={busy}>返回</Button>
            <Button onClick={() => confirm()} disabled={busy || !code} className="flex-1">
              {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}确认
            </Button>
          </div>
        </div>
      )}

      {step === "password" && (
        <div className="space-y-3">
          <p className="text-xs text-[var(--color-warning)]">此账号开启了两步验证，请输入密码</p>
          <div className="space-y-1">
            <label className="text-xs text-[var(--color-muted-foreground)]">两步验证密码</label>
            <Input type="password" value={password} onChange={e => setPassword(e.target.value)} />
          </div>
          <Button onClick={() => confirm(password)} disabled={busy || !password} className="w-full">
            {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}完成登录
          </Button>
        </div>
      )}

      {loggedIn && (
        <div className="space-y-3">
          <div className="rounded-md bg-[var(--color-success)]/10 px-3 py-2 text-xs text-[var(--color-success)]">
            会话已持久化，重启后端无需重新登录
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={refresh} disabled={busy}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />刷新状态
            </Button>
            <Button variant="outline" size="sm" onClick={logout} disabled={busy}>
              <LogOut className="mr-1.5 h-3.5 w-3.5" />退出登录
            </Button>
            <div className="ml-auto">
              <Button size="sm" onClick={sync} disabled={syncing}>
                {syncing && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}立即同步
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
