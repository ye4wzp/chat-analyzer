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

  const syncWechat = (mode: "incremental" | "full" | "repair") => run({
    start: () => fetchAPI<{ task_id: string }>("/sync/wechat", { method: "POST", body: JSON.stringify({ mode }) }),
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
          <Button size="sm" onClick={() => void syncWechat("incremental")} disabled={syncingWechat}>
            {syncingWechat ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />}
            同步新增
          </Button>
        </div>
        <div className="mt-3 flex gap-2">
          <Button size="sm" variant="outline" disabled={syncingWechat} onClick={() => void syncWechat("full")}>全量读取</Button>
          <Button size="sm" variant="outline" disabled={syncingWechat} onClick={() => void syncWechat("repair")}>检查历史补缺</Button>
        </div>
        <p className="mt-3 text-xs text-[var(--color-muted-foreground)] leading-relaxed">
          全量读取和补缺会回查历史及现有本机缓存，保留已有记录，不代表恢复已删除或其他设备独有的历史。需要本地已安装 <code className="font-mono">wx</code> 命令行工具并登录微信。
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
