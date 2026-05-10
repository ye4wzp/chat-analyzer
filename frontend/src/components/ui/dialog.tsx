import * as React from "react"
import { createPortal } from "react-dom"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"

interface DialogProps {
  open: boolean
  onClose: () => void
  title?: React.ReactNode
  description?: React.ReactNode
  children: React.ReactNode
  className?: string
  size?: "sm" | "md" | "lg" | "xl"
}

const sizeClass: Record<NonNullable<DialogProps["size"]>, string> = {
  sm: "max-w-sm",
  md: "max-w-md",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
}

export function Dialog({ open, onClose, title, description, children, className, size = "md" }: DialogProps) {
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

  return createPortal(
    <div className="fixed inset-0 z-[45] flex items-end justify-center md:items-center" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/60 ca-fade-in" onClick={onClose} />
      <div
        className={cn(
          "relative w-full bg-[var(--color-card-elevated)] shadow-2xl ca-slide-up md:ca-fade-in",
          "rounded-t-2xl md:rounded-2xl md:my-4",
          sizeClass[size],
          className,
        )}
      >
        {(title || description) && (
          <div className="border-b border-[var(--color-border)] px-6 py-4">
            {title && <div className="text-base font-semibold">{title}</div>}
            {description && <div className="mt-1 text-sm text-[var(--color-muted-foreground)]">{description}</div>}
          </div>
        )}
        <button
          onClick={onClose}
          aria-label="关闭"
          className="absolute right-3 top-3 rounded-md p-1.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]"
        >
          <X className="h-4 w-4" />
        </button>
        <div className="max-h-[80vh] overflow-y-auto p-6">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
