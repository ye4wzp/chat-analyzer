import { NavLink, Outlet, useNavigate } from "react-router-dom"
import { LayoutDashboard, MessagesSquare, Settings, Search, RefreshCw, Upload, Sparkles, BookOpen, Clock, Menu, X } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, subscribeSSE, type TaskProgress, type PendingKnowledge } from "@/lib/api"
import { DetailPanelProvider, useDetailPanel } from "@/lib/DetailPanelContext"
import { KnowledgeReviewModal } from "@/components/KnowledgeReviewModal"
import { GlobalTaskBar } from "@/components/GlobalTaskBar"
import { Sheet, SheetHeader } from "@/components/ui/sheet"

const nav = [
  { to: "/", label: "首页", icon: LayoutDashboard },
  { to: "/chats", label: "聊天", icon: MessagesSquare },
  { to: "/timeline", label: "时间线", icon: Clock },
  { to: "/knowledge", label: "知识库", icon: BookOpen },
  { to: "/settings", label: "设置", icon: Settings },
]

const TASK_TITLE: Record<string, string> = {
  "/sync/wechat": "微信同步",
  "/sync/all": "全平台同步",
  "/analyze": "AI 分析",
}

// Maps the runTask endpoint to the backend task type used in /api/tasks payloads.
// Keeps the per-endpoint lock semantically aligned with the server's notion of "running".
const ENDPOINT_TASK_TYPE: Record<string, string> = {
  "/sync/wechat": "sync_wechat",
  "/analyze": "analyze",
}

function Inner() {
  const { content, close } = useDetailPanel()
  const navigate = useNavigate()
  const [pendingKnowledge, setPendingKnowledge] = useState<PendingKnowledge[] | null>(null)
  const [pendingTaskId, setPendingTaskId] = useState("")
  const [pendingSummary, setPendingSummary] = useState("")
  // Per-type lock: a running wechat sync no longer blocks an analyze trigger.
  const [runningTypes, setRunningTypes] = useState<Set<string>>(new Set())
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const esRef = useRef<EventSource | null>(null)

  // Mobile detail sheet auto-opens when content arrives, auto-closes on close()
  useEffect(() => { if (content) setDetailOpen(true) }, [content])

  const runSyncAll = async () => {
    const toastId = toast.loading("已触发：微信 + QQ + Telegram 同步")
    try {
      const res = await fetchAPI<{ tasks: Array<{ type: string; task_id: string }> }>(
        "/sync/all", { method: "POST" }
      )
      if (res.tasks.length === 0) {
        toast.info("没有可启动的同步（可能正在运行）", { id: toastId })
      } else {
        const names = res.tasks.map(t => ({
          sync_wechat: "微信", sync_qq: "QQ", sync_telegram: "Telegram",
        }[t.type] ?? t.type)).join(" + ")
        toast.success(`已启动: ${names}（在底部任务栏查看进度）`, { id: toastId })
      }
    } catch (e: unknown) {
      toast.error(getErrorMessage(e), { id: toastId })
    }
  }

  const runTask = async (endpoint: string, body?: Record<string, string>) => {
    const taskType = ENDPOINT_TASK_TYPE[endpoint] ?? endpoint
    if (runningTypes.has(taskType)) return
    setRunningTypes(prev => new Set(prev).add(taskType))
    const release = () => setRunningTypes(prev => {
      const next = new Set(prev); next.delete(taskType); return next
    })
    const title = TASK_TITLE[endpoint] || "任务"
    const toastId = toast.loading(`${title}启动中...`)
    try {
      const { task_id } = await fetchAPI<{ task_id: string }>(endpoint, {
        method: "POST",
        body: body ? JSON.stringify(body) : undefined,
      })
      esRef.current?.close()
      esRef.current = subscribeSSE<TaskProgress>(`/tasks/${task_id}/events`, async (data) => {
        toast.loading(data.message || data.status, { id: toastId })
        if (data.status === "done") {
          esRef.current?.close()
          if (endpoint === "/analyze") {
            try {
              const results = await fetchAPI<PendingKnowledge[]>(`/analyze/${task_id}/results`)
              setPendingKnowledge(results)
              setPendingTaskId(task_id)
              setPendingSummary(data.summary || "")
            } catch { /* ignore */ }
          }
          toast.success(data.message || "完成", { id: toastId })
          release()
        } else if (data.status === "error") {
          esRef.current?.close()
          toast.error(data.message || "失败", { id: toastId })
          release()
        } else if (data.status === "cancelled") {
          esRef.current?.close()
          toast.info(data.message || "已取消", { id: toastId })
          release()
        }
      }, () => {
        toast.dismiss(toastId)
        release()
      })
    } catch (e: unknown) {
      toast.error(getErrorMessage(e), { id: toastId })
      release()
    }
  }

  return (
    <div className="flex h-screen bg-[var(--color-background)]">
      {/* 移动端遮罩 */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-30 bg-black/50 md:hidden ca-fade-in" onClick={() => setSidebarOpen(false)} />
      )}

      {/* 侧边栏 */}
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-60 shrink-0 flex-col bg-[var(--color-sidebar)] border-r border-[var(--color-border)] transition-transform md:static md:translate-x-0 ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="flex items-center gap-2 px-4 pt-5 pb-4">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[var(--color-primary)]/15 text-[var(--color-primary)]">
            <MessagesSquare className="h-4 w-4" />
          </div>
          <span className="text-sm font-semibold">聊天分析器</span>
        </div>

        <div className="px-3 mb-2">
          <button
            onClick={() => { navigate("/search"); setSidebarOpen(false) }}
            className="flex w-full items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-1.5 text-sm transition-colors text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)] hover:text-[var(--color-foreground)]"
          >
            <Search className="h-3.5 w-3.5" />
            <span>搜索</span>
            <span className="ml-auto text-xs opacity-50">⌘K</span>
          </button>
        </div>

        <nav className="flex flex-col gap-0.5 px-3">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors ${isActive ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)] hover:text-[var(--color-foreground)]"}`
              }
            >
              <Icon className="h-3.5 w-3.5 shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="mt-auto px-3 pb-4 border-t border-[var(--color-border)] pt-4 flex flex-col gap-0.5">
          <p className="px-3 mb-1 text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]/60">操作</p>
          <button
            onClick={() => { void runSyncAll() }}
            className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors text-left text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)] hover:text-[var(--color-foreground)]"
          >
            <RefreshCw className="h-3.5 w-3.5 shrink-0" />
            同步全部
          </button>
          <button
            onClick={() => { navigate("/settings?tab=sources"); setSidebarOpen(false) }}
            className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors text-left text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)] hover:text-[var(--color-foreground)]"
          >
            <Upload className="h-3.5 w-3.5 shrink-0" />
            数据源
          </button>
          <button
            onClick={() => { void runTask("/analyze") }}
            disabled={runningTypes.has("analyze")}
            className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors text-left text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)] hover:text-[var(--color-foreground)] disabled:opacity-50 disabled:pointer-events-none"
          >
            <Sparkles className="h-3.5 w-3.5 shrink-0" />
            运行分析
          </button>
        </div>
      </aside>

      {/* 主内容 */}
      <main className="flex flex-1 flex-col overflow-hidden bg-[var(--color-background)]">
        {/* 移动端顶栏 */}
        <div className="flex items-center gap-3 border-b border-[var(--color-border)] px-4 py-2 md:hidden">
          <button onClick={() => setSidebarOpen(true)} aria-label="打开菜单" className="text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
            <Menu className="h-5 w-5" />
          </button>
          <span className="text-sm font-semibold">聊天分析器</span>
        </div>
        <GlobalTaskBar />
        <div className="flex-1 overflow-auto">
          <Outlet />
        </div>
      </main>

      {/* 桌面端详情面板 */}
      {content && (
        <aside className="hidden md:flex w-80 shrink-0 flex-col overflow-y-auto bg-[var(--color-sidebar)] border-l border-[var(--color-border)]">
          <div className="flex items-center justify-end px-4 py-3 border-b border-[var(--color-border)]">
            <button
              onClick={close}
              aria-label="关闭详情"
              className="rounded p-1 text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="p-5">{content}</div>
        </aside>
      )}

      {/* 移动端详情 BottomSheet */}
      <Sheet
        open={detailOpen && !!content}
        onClose={() => { setDetailOpen(false); close() }}
        side="bottom"
        mobileOnly
      >
        <SheetHeader title="详情" onClose={() => { setDetailOpen(false); close() }} />
        <div className="overflow-y-auto p-5">{content}</div>
      </Sheet>

      {pendingKnowledge && (
        <KnowledgeReviewModal
          taskId={pendingTaskId}
          results={pendingKnowledge}
          summary={pendingSummary}
          onClose={() => setPendingKnowledge(null)}
          onSaved={() => setPendingKnowledge(null)}
        />
      )}
    </div>
  )
}

export default function Layout() {
  return (
    <DetailPanelProvider>
      <Inner />
    </DetailPanelProvider>
  )
}
