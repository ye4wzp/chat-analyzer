import { NavLink } from "react-router-dom"

/**
 * Segmented switch between sibling pages that share one sidebar entry
 * (e.g. 周报月报 ⇄ 年度回顾, 联系人档案 ⇄ 标签库). Keeps the sidebar short
 * without folding two large pages into one component.
 */
export function SubNav({ items }: { items: { to: string; label: string }[] }) {
  return (
    <nav className="inline-flex items-center gap-1 rounded-lg bg-[var(--color-muted)] p-1 text-sm" aria-label="子页面">
      {items.map(item => (
        <NavLink
          key={item.to}
          to={item.to}
          end
          className={({ isActive }) =>
            `rounded-md px-3 py-1.5 transition-colors ${isActive ? "bg-[var(--color-card-elevated)] font-medium shadow-sm" : "text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"}`
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}
