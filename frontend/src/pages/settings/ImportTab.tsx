import { fetchAPI, type Config } from "@/lib/api"
import { useTaskRunner } from "@/hooks/useTaskRunner"
import { Button } from "@/components/ui/button"
import { Loader2, RefreshCw, MessagesSquare } from "lucide-react"
import { QQCard } from "./QQCard"
import { TelegramCard } from "./TelegramCard"
import { AliasesCard } from "./AliasesCard"

interface Props {
  config: Config
  onConfigChange: (cfg: Config) => void
}

export function ImportTab({ config, onConfigChange }: Props) {
  const { run, running } = useTaskRunner()
  const syncingWechat = running.has("sync_wechat")

  const syncWechat = () => run({
    start: () => fetchAPI<{ task_id: string }>("/sync/wechat", { method: "POST" }),
    taskType: "sync_wechat",
    loadingText: "微信同步启动中...",
  })

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
          <Button size="sm" onClick={() => void syncWechat()} disabled={syncingWechat}>
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
      <AliasesCard />

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
