import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import {
  fetchAPI,
  getErrorMessage,
  subscribeSSE,
  type TaskProgress,
  type TelegramStatus,
  type TelegramLoginConfirmResponse,
  type TelegramQRStartResponse,
  type TelegramQRStatusResponse,
} from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Loader2, ExternalLink, LogOut, RefreshCw, ArrowRight, QrCode, Smartphone } from "lucide-react"

type Step = "idle" | "credentials" | "code-phone" | "qr" | "qr_password" | "code" | "password" | "done"

const QR_POLL_MS = 2000

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

  // QR-specific state
  const [qrId, setQrId] = useState("")
  const [qrPng, setQrPng] = useState("")
  const [qrExpiresAt, setQrExpiresAt] = useState(0)
  const [qrSecondsLeft, setQrSecondsLeft] = useState(0)
  const qrPollRef = useRef<number | null>(null)
  const stepRef = useRef<Step>("idle")
  stepRef.current = step

  useEffect(() => { void refresh() }, [])

  // Stop polling on unmount or whenever we leave the QR step.
  useEffect(() => {
    if (step !== "qr" && qrPollRef.current !== null) {
      window.clearInterval(qrPollRef.current)
      qrPollRef.current = null
    }
    return () => {
      if (qrPollRef.current !== null) {
        window.clearInterval(qrPollRef.current)
        qrPollRef.current = null
      }
    }
  }, [step])

  // Tick the displayed countdown each second; expiry is enforced by the backend.
  useEffect(() => {
    if (step !== "qr" || !qrExpiresAt) return
    const tick = () => setQrSecondsLeft(Math.max(0, Math.floor(qrExpiresAt - Date.now() / 1000)))
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [step, qrExpiresAt])

  const refresh = async () => {
    try {
      const s = await fetchAPI<TelegramStatus>("/telegram/status")
      setStatus(s)
      setStep(s.logged_in ? "done" : "idle")
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  const startSmsLogin = async () => {
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

  const startQrLogin = async () => {
    setBusy(true)
    try {
      const res = await fetchAPI<TelegramQRStartResponse>("/telegram/login/qr/start", {
        method: "POST",
        body: JSON.stringify({ api_id: Number(apiId), api_hash: apiHash }),
      })
      setQrId(res.qr_id)
      setQrPng(res.qr_png)
      setQrExpiresAt(res.expires_at)
      setStep("qr")
      // Start polling.
      if (qrPollRef.current !== null) window.clearInterval(qrPollRef.current)
      qrPollRef.current = window.setInterval(() => { void pollQr(res.qr_id) }, QR_POLL_MS)
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const pollQr = async (id: string) => {
    // Guard: user might have navigated away between schedule and execution.
    if (stepRef.current !== "qr" && stepRef.current !== "qr_password") return
    try {
      const res = await fetchAPI<TelegramQRStatusResponse>(`/telegram/login/qr/status/${id}`)
      if (res.status === "success") {
        toast.success(`已登录 ${res.username || res.first_name || ""}`)
        await refresh()
      } else if (res.status === "password_needed") {
        setStep("qr_password")
      } else if (res.status === "expired") {
        toast.error("二维码已过期，请重新生成")
        setStep("credentials")
      } else if (res.status === "error") {
        toast.error(res.error || "登录失败")
        setStep("credentials")
      }
    } catch {
      // Transient network errors during polling shouldn't kick the user out;
      // the next tick will retry and the backend will tell us if expired.
    }
  }

  const submitQrPassword = async () => {
    setBusy(true)
    try {
      const res = await fetchAPI<TelegramLoginConfirmResponse>("/telegram/login/qr/password", {
        method: "POST",
        body: JSON.stringify({ qr_id: qrId, password }),
      })
      toast.success(`已登录 ${res.username || res.first_name || ""}`)
      await refresh()
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const confirmCode = async (withPassword?: string) => {
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
            扫码或验证码登录后自动拉取所有对话历史
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
          <div className="grid grid-cols-2 gap-2 pt-1">
            <Button
              onClick={startQrLogin}
              disabled={busy || !apiId || !apiHash}
              className="w-full"
            >
              {busy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <QrCode className="mr-1.5 h-3.5 w-3.5" />}
              扫码登录
            </Button>
            <Button
              variant="outline"
              onClick={() => setStep("code-phone")}
              disabled={busy}
              className="w-full"
            >
              <Smartphone className="mr-1.5 h-3.5 w-3.5" />
              短信验证码
            </Button>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setStep("idle")} disabled={busy} className="w-full">返回</Button>
        </div>
      )}

      {/* SMS phone-input panel — split out of the original credentials step so QR users don't see this */}
      {step === "code-phone" && (
        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs text-[var(--color-muted-foreground)]">手机号（含国家码）</label>
            <Input value={phone} onChange={e => setPhone(e.target.value)} placeholder="+8613800138000" />
          </div>
          <div className="flex gap-2 pt-1">
            <Button variant="outline" size="sm" onClick={() => setStep("credentials")} disabled={busy}>返回</Button>
            <Button onClick={startSmsLogin} disabled={busy || !phone} className="flex-1">
              {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              发送验证码 <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}

      {step === "qr" && (
        <div className="space-y-3">
          <div className="rounded-md border border-[var(--color-border)] bg-white p-3 flex flex-col items-center gap-2">
            {qrPng ? (
              <img src={qrPng} alt="Telegram QR login" className="h-48 w-48" />
            ) : (
              <Loader2 className="h-8 w-8 animate-spin text-[var(--color-muted-foreground)] my-16" />
            )}
            <p className="text-xs text-[var(--color-muted-foreground)] text-center leading-relaxed">
              在 Telegram 手机客户端 → <span className="font-medium">设置 → 设备 → 扫描二维码</span> 扫描登录
            </p>
            <p className="text-xs tabular-nums text-[var(--color-muted-foreground)]">
              {qrSecondsLeft > 0 ? `${qrSecondsLeft}s 后过期` : "已过期"}
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setStep("credentials")} disabled={busy}>返回</Button>
            <Button variant="outline" size="sm" onClick={startQrLogin} disabled={busy} className="flex-1">
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />重新生成
            </Button>
          </div>
        </div>
      )}

      {step === "qr_password" && (
        <div className="space-y-3">
          <p className="text-xs text-[var(--color-warning)]">扫码成功，但此账号开启了两步验证，请输入密码完成登录</p>
          <div className="space-y-1">
            <label className="text-xs text-[var(--color-muted-foreground)]">两步验证密码</label>
            <Input type="password" value={password} onChange={e => setPassword(e.target.value)} />
          </div>
          <Button onClick={submitQrPassword} disabled={busy || !password} className="w-full">
            {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}完成登录
          </Button>
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
            <Button onClick={() => confirmCode()} disabled={busy || !code} className="flex-1">
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
          <Button onClick={() => confirmCode(password)} disabled={busy || !password} className="w-full">
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
