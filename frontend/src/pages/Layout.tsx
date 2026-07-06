import { NavLink, Outlet, useNavigate } from "react-router-dom"
import { LayoutDashboard, MessagesSquare, Settings, Search, RefreshCw, Upload, Sparkles, BookOpen, Clock, Menu, X, Inbox, Play, Tags as TagsIcon, ListChecks, Sun, Moon } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { toast, Toaster } from "sonner"
import { fetchAPI, getErrorMessage, type SyncHealth } from "@/lib/api"
import { DetailPanelProvider, useDetailPanel } from "@/lib/DetailPanelContext"
import { GlobalEventsProvider, useGlobalEvents } from "@/lib/GlobalEventsContext"
import { formatRelative } from "@/lib/utils"
import { useAnalyzeRunner } from "@/hooks/useAnalyzeRunner"
import { KnowledgeReviewModal } from "@/components/KnowledgeReviewModal"
import { GlobalTaskBar } from "@/components/GlobalTaskBar"
import { NotificationBell } from "@/components/NotificationBell"
import { Sheet, SheetHeader } from "@/components/ui/sheet"

const nav = [
  { to: "/", label: "首页", icon: LayoutDashboard },
  { to: "/chats", label: "聊天", icon: MessagesSquare },
  { to: "/timeline", label: "时间线", icon: Clock },
  { to: "/inbox", label: "收件箱", icon: Inbox },
  { to: "/knowledge", label: "知识库", icon: BookOpen },
  { to: "/tags", label: "标签", icon: TagsIcon },
  { to: "/todos", label: "待办", icon: ListChecks },
  { to: "/settings", label: "设置", icon: Settings },
]

function SyncStatusCard({ onSync }: { onSync: () => void }) {
  const { tasks } = useGlobalEvents()
  const [health, setHealth] = useState<SyncHealth | null>(null)

  const load = useCallback(() => {
    fetchAPI<SyncHealth>("/sync/health").then(setHealth).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  // Refresh whenever a sync task reaches "done".
  const syncDoneKey = tasks
    .filter(t => t.type.startsWith("sync") && t.status === "done")
    .map(t => t.id)
    .join(",")
  useEffect(() => { if (syncDoneKey) load() }, [syncDoneKey, load])

  const platforms = health?.platforms ?? []
  const lastSync = platforms
    .map(p => p.last_sync)
    .filter((v): v is string => !!v)
    .sort()
    .pop()

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium">
        <span className={`h-1.5 w-1.5 rounded-full ${platforms.length ? "bg-[var(--color-success)]" : "bg-[var(--color-muted-foreground)]"}`} />
        数据状态
        {platforms.length > 0 && (
          <span className="ml-auto rounded-full bg-[var(--color-success-subtle)] px-2 py-0.5 text-[10px] text-[var(--color-success)]">已同步</span>
        )}
      </div>
      <div className="space-y-1 text-xs text-[var(--color-muted-foreground)]">
        {platforms.length === 0 ? (
          <p>尚未同步</p>
        ) : (
          <>
            <div className="flex justify-between"><span>最后同步</span><span>{lastSync ? formatRelative(lastSync) : "未知"}</span></div>
            <div className="flex justify-between"><span>数据源</span><span>{platforms.length} 个平台</span></div>
          </>
        )}
      </div>
      <button
        onClick={onSync}
        className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card-elevated)] px-3 py-2 text-xs font-medium text-[var(--color-foreground)] transition-colors hover:bg-[var(--color-accent)]"
      >
        <RefreshCw className="h-3.5 w-3.5" />
        立即同步
      </button>
    </div>
  )
}

function Inner() {
  const { content, close } = useDetailPanel()
  const navigate = useNavigate()
  const analyze = useAnalyzeRunner()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [theme, setTheme] = useState<"dark" | "light">(
    () => (typeof document !== "undefined" && document.documentElement.dataset.theme === "light") ? "light" : "dark",
  )

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark"
    document.documentElement.dataset.theme = next
    try { localStorage.setItem("ca-theme", next) } catch { /* private mode */ }
    setTheme(next)
  }

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
          sync_wechat: "微信", sync_qq: "QQ", sync_telegram: "Telegram", sync_all: "全平台",
        }[t.type] ?? t.type)).join(" + ")
        toast.success(`已启动: ${names}（在底部任务栏查看进度）`, { id: toastId })
      }
    } catch (e: unknown) {
      toast.error(getErrorMessage(e), { id: toastId })
    }
  }

  return (
    <div className="flex h-screen bg-[var(--color-background)] text-[var(--color-foreground)]">
      {/* 移动端遮罩 */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-30 bg-black/50 md:hidden ca-fade-in" onClick={() => setSidebarOpen(false)} />
      )}

      {/* 侧边栏 */}
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-56 shrink-0 flex-col bg-[var(--color-sidebar)] border-r border-[var(--color-border)] shadow-[1px_0_18px_rgba(15,23,42,0.04)] transition-transform md:static md:translate-x-0 ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="flex items-center gap-3 px-5 pt-6 pb-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--color-primary)] text-[var(--color-primary-foreground)] shadow-sm">
            <MessagesSquare className="h-4.5 w-4.5" />
          </div>
          <div className="min-w-0 flex-1">
            <span className="block text-[15px] font-semibold tracking-tight">Chat Analyzer</span>
            <span className="block text-xs text-[var(--color-muted-foreground)]">本地分析工具</span>
          </div>
          <NotificationBell />
        </div>

        <nav className="flex flex-col gap-1 px-3 pt-2">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3.5 py-2.5 text-sm transition-all ${isActive ? "bg-[var(--color-accent)] text-[var(--color-primary)] shadow-sm" : "text-[var(--color-muted-foreground)] hover:bg-[var(--color-card)] hover:text-[var(--color-foreground)]"}`
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="mt-auto px-4 pb-4 pt-4 flex flex-col gap-3">
          <SyncStatusCard onSync={() => { void runSyncAll() }} />
          <div className="border-t border-[var(--color-border)] pt-3 flex flex-col gap-1">
          <button
            onClick={() => { void runSyncAll() }}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors text-left text-[var(--color-muted-foreground)] hover:bg-[var(--color-card)] hover:text-[var(--color-foreground)]"
          >
            <RefreshCw className="h-4 w-4 shrink-0" />
            同步全部
          </button>
          <button
            onClick={() => { navigate("/settings?tab=sources"); setSidebarOpen(false) }}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors text-left text-[var(--color-muted-foreground)] hover:bg-[var(--color-card)] hover:text-[var(--color-foreground)]"
          >
            <Upload className="h-4 w-4 shrink-0" />
            数据源
          </button>
          <button
            onClick={() => { void analyze.run() }}
            disabled={analyze.running}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors text-left text-[var(--color-muted-foreground)] hover:bg-[var(--color-card)] hover:text-[var(--color-foreground)] disabled:opacity-50 disabled:pointer-events-none"
          >
            <Sparkles className="h-4 w-4 shrink-0" />
            运行分析
          </button>
          </div>
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
        <div className="hidden h-[74px] shrink-0 items-center gap-4 border-b border-[var(--color-border)] bg-[var(--color-background)]/80 px-6 backdrop-blur md:flex">
          <button
            onClick={() => navigate("/search")}
            className="flex h-11 min-w-[320px] max-w-[520px] flex-1 items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 text-left text-sm text-[var(--color-muted-foreground)] shadow-sm transition-colors hover:border-[var(--color-border-strong)] hover:bg-[var(--color-card-elevated)]"
          >
            <Search className="h-4 w-4" />
            <span className="flex-1">搜索群聊、联系人、关键词...</span>
          </button>
          <button
            onClick={toggleTheme}
            title={theme === "dark" ? "切换到浅色" : "切换到深色"}
            aria-label="切换主题"
            className="flex h-11 w-11 items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] text-[var(--color-muted-foreground)] shadow-sm transition-colors hover:bg-[var(--color-accent)] hover:text-[var(--color-foreground)]"
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
          <button
            onClick={() => { void runSyncAll() }}
            className="flex h-11 items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 text-sm font-medium text-[var(--color-foreground)] shadow-sm transition-colors hover:bg-[var(--color-accent)]"
          >
            <RefreshCw className="h-4 w-4" />
            同步
          </button>
          <button
            onClick={() => { void analyze.run() }}
            disabled={analyze.running}
            className="flex h-11 items-center gap-2 rounded-lg bg-[var(--color-primary)] px-5 text-sm font-semibold text-[var(--color-primary-foreground)] shadow-md transition-colors hover:bg-[var(--color-primary-hover)] disabled:opacity-50"
          >
            <Play className="h-4 w-4 fill-current" />
            运行分析
          </button>
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

      {analyze.modalProps.results && (
        <KnowledgeReviewModal
          taskId={analyze.modalProps.taskId}
          results={analyze.modalProps.results}
          summary={analyze.modalProps.summary}
          onClose={analyze.modalProps.onClose}
          onSaved={analyze.modalProps.onSaved}
        />
      )}

      <Toaster theme={theme} richColors position="bottom-right" closeButton />
    </div>
  )
}

export default function Layout() {
  return (
    <DetailPanelProvider>
      <GlobalEventsProvider>
        <Inner />
      </GlobalEventsProvider>
    </DetailPanelProvider>
  )
}
