import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Backend emits both ISO strings and "YYYY-MM-DD HH:MM:SS" — normalize before parsing.
export function parseTime(ts: string): Date {
  return new Date(/^\d{4}-\d{2}-\d{2} /.test(ts) ? ts.replace(" ", "T") : ts)
}

export function formatTime(ts: string) {
  return parseTime(ts).toLocaleString()
}

export function formatRelative(ts: string): string {
  const t = parseTime(ts).getTime()
  if (Number.isNaN(t)) return "未知"
  const diff = Date.now() - t
  if (diff < 60_000) return "刚刚"
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return `${Math.floor(diff / 86_400_000)} 天前`
}
