import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { LayoutDashboard, MessagesSquare, Settings, Search, RefreshCw, Upload, Sparkles, BookOpen, Clock, Menu, X, Inbox, Play, ListChecks, Sun, Moon, FileText, BarChart3, Contact, Users, CalendarDays, Link2, AtSign, Gift } from "lucide-react"
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

const navSections: { title?: string; items: { to: string; label: string; icon: typeof LayoutDashboard }[] }[] = [
  {
    items: [
      { to: "/", label: "首页", icon: LayoutDashboard },
      { to: "/overview", label: "数据概览", icon: BarChart3 },
      { to: "/ask", label: "AI 问答", icon: Sparkles },
    ],
  },
  {
    title: "浏览",
    items: [
      { to: "/chats", label: "聊天浏览", icon: MessagesSquare },
      { to: "/timeline", label: "时间线", icon: Clock },
      { to: "/inbox", label: "收件箱", icon: Inbox },
      { to: "/mentions", label: "@消息", icon: AtSign },
      { to: "/resources", label: "资源汇总", icon: Link2 },
      { to: "/payments", label: "红包转账", icon: Gift },
    ],
  },
  {
    title: "分析",
    items: [
      { to: "/contacts", label: "联系人档案", icon: Contact },
      { to: "/members", label: "成员分析", icon: Users },
      { to: "/calendar", label: "日历热图", icon: CalendarDays },
      { to: "/knowledge", label: "知识库", icon: BookOpen },
      { to: "/todos", label: "待办", icon: ListChecks },
      { to: "/reports", label: "报告", icon: FileText },
    ],
  },
  {
    items: [
      { to: "/settings", label: "设置", icon: Settings },
    ],
  },
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
    .filter(t => t.type.startsWith("sync") && ["done", "error", "partial", "interrupted", "cancelled"].includes(t.status))
    .map(t => t.id)
    .join(",")
  useEffect(() => { if (syncDoneKey) load() }, [syncDoneKey, load])

  const incomplete = health?.runs?.some(r => r.status !== "done") ?? false
  const platforms = health?.platforms ?? []
  const lastSync = platforms
    .map(p => p.last_sync)
    .filter((v): v is string => !!v)
    .sort()
    .pop()

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium">
        <span className={`h-1.5 w-1.5 rounded-full ${incomplete ? "bg-amber-500" : platforms.length ? "bg-[var(--color-success)]" : "bg-[var(--color-muted-foreground)]"}`} />
        数据状态
        {platforms.length > 0 && (
          <span className="ml-auto rounded-full px-2 py-0.5 text-xs" style={{ color: incomplete ? "var(--color-warning)" : "var(--color-success)", background: incomplete ? "var(--color-warning-subtle)" : "var(--color-success-subtle)" }}>{incomplete ? "有未完成项" : "已有记录"}</span>
        )}
      </div>
      <div className="space-y-1 text-xs text-[var(--color-muted-foreground)]">
        {platforms.length === 0 ? (
          <p>尚未同步</p>
        ) : (
          <>
            <div className="flex justify-between"><span>最近数据写入</span><span>{lastSync ? formatRelative(lastSync) : "未知"}</span></div>
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

// Pages reachable only via an in-page SubNav; the sidebar keeps their parent lit.
const SIBLING_OF: Record<string, string> = { "/yearly": "/reports", "/tags": "/contacts" }

type Theme = "light" | "dark"

function ThemeSwitch({ theme, onChange }: { theme: Theme; onChange: (theme: Theme) => void }) {
  return (
    <div role="group" aria-label="界面主题" className="flex shrink-0 gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-1 shadow-sm">
      {([{ value: "light", label: "浅色", icon: Sun }, { value: "dark", label: "深色", icon: Moon }] as const).map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          aria-pressed={theme === value}
          aria-label={`${label}主题`}
          onClick={() => onChange(value)}
          className={`flex items-center gap-1.5 rounded-md px-2.5 py-2 text-xs font-medium transition-colors ${theme === value ? "bg-[var(--color-primary)] text-[var(--color-primary-foreground)] shadow-sm" : "text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)] hover:text-[var(--color-foreground)]"}`}
        >
          <Icon aria-hidden="true" className="h-3.5 w-3.5" />
          {label}
        </button>
      ))}
    </div>
  )
}

function Inner() {
  const { content, close } = useDetailPanel()
  const navigate = useNavigate()
  const location = useLocation()
  const analyze = useAnalyzeRunner()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [theme, setTheme] = useState<Theme>(
    () => (typeof document !== "undefined" && document.documentElement.dataset.theme === "dark") ? "dark" : "light",
  )

  const changeTheme = (next: Theme) => {
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
    <div className="flex h-dvh bg-[var(--color-background)] text-[var(--color-foreground)]">
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

        <nav className="ca-scroll flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-3 pt-1 pb-2">
          {navSections.map((section, si) => (
            <div key={si} className={si > 0 ? "mt-2" : ""}>
              {section.title && (
                <p className="px-3.5 pb-1 pt-1 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-faint)]">{section.title}</p>
              )}
              {section.items.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={to === "/"}
                  onClick={() => setSidebarOpen(false)}
                  className={({ isActive }) =>
                    `flex items-center gap-3 rounded-lg px-3.5 py-2 text-sm transition-all ${isActive || SIBLING_OF[location.pathname] === to ? "bg-[var(--color-accent)] text-[var(--color-primary)] shadow-sm" : "text-[var(--color-muted-foreground)] hover:bg-[var(--color-card)] hover:text-[var(--color-foreground)]"}`
                  }
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="px-4 pb-4 pt-3 flex flex-col gap-3 border-t border-[var(--color-border)]">
          <SyncStatusCard onSync={() => { void runSyncAll() }} />
          <div className="grid grid-cols-2 gap-1">
            <button
              onClick={() => { navigate("/settings?tab=sources"); setSidebarOpen(false) }}
              className="flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs transition-colors text-[var(--color-muted-foreground)] hover:bg-[var(--color-card)] hover:text-[var(--color-foreground)]"
            >
              <Upload className="h-3.5 w-3.5 shrink-0" />
              数据源
            </button>
            <button
              onClick={() => { void analyze.run() }}
              disabled={analyze.running}
              className="flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs transition-colors text-[var(--color-muted-foreground)] hover:bg-[var(--color-card)] hover:text-[var(--color-foreground)] disabled:opacity-50 disabled:pointer-events-none"
            >
              <Sparkles className="h-3.5 w-3.5 shrink-0" />
              运行分析
            </button>
          </div>
        </div>
      </aside>

      {/* 主内容 */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--color-background)]">
        {/* 移动端顶栏 */}
        <div className="flex items-center gap-3 border-b border-[var(--color-border)] px-4 py-2 md:hidden">
          <button onClick={() => setSidebarOpen(true)} aria-label="打开菜单" className="text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
            <Menu className="h-5 w-5" />
          </button>
          <span className="flex-1 text-sm font-semibold">{navSections.flatMap(s => s.items).find(i => i.to === location.pathname)?.label ?? ({ "/search": "搜索", "/yearly": "年度报告", "/tags": "标签" }[location.pathname] || "聊天分析器")}</span>
          <ThemeSwitch theme={theme} onChange={changeTheme} />
        </div>
        <div className="hidden h-[74px] shrink-0 items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-background)]/80 px-4 backdrop-blur md:flex lg:gap-4 lg:px-6">
          <button
            onClick={() => navigate("/search")}
            className="flex h-11 min-w-0 max-w-[520px] flex-1 items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 text-left text-sm text-[var(--color-muted-foreground)] shadow-sm transition-colors hover:border-[var(--color-border-strong)] hover:bg-[var(--color-card-elevated)]"
          >
            <Search className="h-4 w-4 shrink-0" />
            <span className="flex-1 truncate">搜索群聊、联系人、关键词...</span>
          </button>
          <ThemeSwitch theme={theme} onChange={changeTheme} />
          <button
            onClick={() => { void runSyncAll() }}
            className="flex h-11 shrink-0 items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 text-sm font-medium text-[var(--color-foreground)] shadow-sm transition-colors hover:bg-[var(--color-accent)]"
          >
            <RefreshCw className="h-4 w-4" />
            同步
          </button>
          <button
            onClick={() => { void analyze.run() }}
            disabled={analyze.running}
            className="flex h-11 shrink-0 items-center gap-2 rounded-lg bg-[var(--color-primary)] px-5 text-sm font-semibold text-[var(--color-primary-foreground)] shadow-md transition-colors hover:bg-[var(--color-primary-hover)] disabled:opacity-50"
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
