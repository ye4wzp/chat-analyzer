import * as React from "react"
import { cn } from "@/lib/utils"

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "default" | "secondary" | "destructive" | "outline"
}

const variantStyles: Record<string, string> = {
  default: "bg-[var(--color-primary)] text-[var(--color-primary-foreground)]",
  secondary: "bg-[var(--color-secondary)] text-[var(--color-secondary-foreground)]",
  destructive: "bg-[var(--color-destructive)] text-[var(--color-destructive-foreground)]",
  outline: "border border-[var(--color-border)] text-[var(--color-foreground)]",
}

function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold transition-colors",
        variantStyles[variant],
        className,
      )}
      {...props}
    />
  )
}

export { Badge }
