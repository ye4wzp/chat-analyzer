import { useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, subscribeSSE, type Config, type TaskProgress } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Loader2, RefreshCw, MessagesSquare } from "lucide-react"
import { QQCard } from "./QQCard"
import { TelegramCard } from "./TelegramCard"

interface Props {
  config: Config
  onConfigChange: (cfg: Config) => void
}

export function ImportTab({ config, onConfigChange }: Props) {
  const [syncingWechat, setSyncingWechat] = useState(false)

  const syncWechat = async () => {
    if (syncingWechat) return
    setSyncingWechat(true)
    const id = toast.loading("微信同步启动中...")
    try {
      const { task_id } = await fetchAPI<{ task_id: string }>("/sync/wechat", { method: "POST" })
      const es = subscribeSSE<TaskProgress>(`/tasks/${task_id}/events`, (data) => {
        toast.loading(data.message || data.status, { id })
        if (data.status === "done") {
          es.close(); toast.success(data.message || "完成", { id }); setSyncingWechat(false)
        } else if (data.status === "error") {
          es.close(); toast.error(data.message || "失败", { id }); setSyncingWechat(false)
        }
      }, () => { toast.dismiss(id); setSyncingWechat(false) })
    } catch (e) {
      toast.error(getErrorMessage(e), { id })
      setSyncingWechat(false)
    }
  }

  return (
    <div className="space-y-4 max-w-3xl">
      {/* WeChat card (existing wx-cli flow, kept lightweight) */}
      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              <span className="h-2 w-2 rounded-full" style={{ background: "var(--color-platform-wechat)" }} />
              微信
            </h3>
            <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
              通过 wx-cli 同步本地微信会话和历史消息
            </p>
          </div>
          <Button size="sm" onClick={syncWechat} disabled={syncingWechat}>
            {syncingWechat ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />}
            立即同步
          </Button>
        </div>
        <p className="mt-3 text-xs text-[var(--color-muted-foreground)] leading-relaxed">
          需要本地已安装 <code className="font-mono">wx</code> 命令行工具并登录微信。
        </p>
      </div>

      <QQCard config={config} onConfigChange={onConfigChange} />
      <TelegramCard />

      <div className="rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-card)]/50 p-5">
        <h3 className="flex items-center gap-2 text-sm font-semibold mb-2">
          <MessagesSquare className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
          兜底：JSON 文件导入
        </h3>
        <p className="text-xs text-[var(--color-muted-foreground)] leading-relaxed">
          如果上述方式不可用，仍可通过命令行调用 <code className="font-mono">/api/import/qq</code> 或
          <code className="font-mono">/api/import/telegram</code> 端点，传入 Telegram Desktop 导出的
          <code className="font-mono">result.json</code> 或 QCE 导出的 JSON 文件路径。
        </p>
      </div>
    </div>
  )
}
