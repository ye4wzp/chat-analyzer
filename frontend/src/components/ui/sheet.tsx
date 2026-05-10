import * as React from "react"
import { createPortal } from "react-dom"
import { cn } from "@/lib/utils"

type Side = "left" | "right" | "bottom"

interface SheetProps {
  open: boolean
  onClose: () => void
  side?: Side
  children: React.ReactNode
  className?: string
  /** Hide on >= md when used only for mobile */
  mobileOnly?: boolean
}

const sideClasses: Record<Side, string> = {
  left: "inset-y-0 left-0 h-full w-72 max-w-[85vw] border-r border-[var(--color-border)]",
  right: "inset-y-0 right-0 h-full w-80 max-w-[85vw] border-l border-[var(--color-border)]",
  bottom: "inset-x-0 bottom-0 max-h-[85vh] w-full rounded-t-2xl border-t border-[var(--color-border)]",
}

const sideAnim: Record<Side, string> = {
  left: "ca-fade-in",
  right: "ca-fade-in",
  bottom: "ca-slide-up",
}

export function Sheet({ open, onClose, side = "bottom", children, className, mobileOnly }: SheetProps) {
  React.useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    document.body.style.overflow = "hidden"
    return () => {
      window.removeEventListener("keydown", onKey)
      document.body.style.overflow = ""
    }
  }, [open, onClose])

  if (!open) return null

  const wrap = mobileOnly ? "md:hidden" : ""

  return createPortal(
    <div className={cn("fixed inset-0 z-[45]", wrap)} role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/60 ca-fade-in" onClick={onClose} />
      <div
        className={cn(
          "absolute bg-[var(--color-card-elevated)] shadow-xl flex flex-col overflow-hidden",
          sideClasses[side],
          sideAnim[side],
          className,
        )}
      >
        {children}
      </div>
    </div>,
    document.body,
  )
}

interface SheetHeaderProps {
  title?: React.ReactNode
  onClose?: () => void
  className?: string
  children?: React.ReactNode
}

export function SheetHeader({ title, onClose, className, children }: SheetHeaderProps) {
  return (
    <div className={cn("flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3 shrink-0", className)}>
      <div className="text-sm font-semibold">{title ?? children}</div>
      {onClose && (
        <button onClick={onClose} aria-label="关闭" className="rounded-md p-1 text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </button>
      )}
    </div>
  )
}
