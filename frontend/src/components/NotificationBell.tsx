import { useNavigate } from "react-router-dom"
import { Bell } from "lucide-react"
import { useGlobalEvents } from "@/lib/GlobalEventsContext"

export function NotificationBell() {
  const { unread: count } = useGlobalEvents()
  const navigate = useNavigate()

  return (
    <button
      onClick={() => navigate("/inbox")}
      title={count > 0 ? `${count} 条未读关键词命中` : "收件箱"}
      className="relative rounded p-1.5 text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] hover:bg-[var(--color-accent)]"
    >
      <Bell className="h-4 w-4" />
      {count > 0 && (
        <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full text-xs font-medium tabular-nums bg-[var(--color-destructive)] text-white flex items-center justify-center">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </button>
  )
}
