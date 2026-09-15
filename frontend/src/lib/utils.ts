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

// 12,345 → "1.2万"; 1,234,567 → "123.5万"; keeps small numbers exact.
export function formatCount(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "0"
  if (Math.abs(n) >= 100_000_000) return `${(n / 100_000_000).toFixed(1)}亿`
  if (Math.abs(n) >= 10_000) return `${(n / 10_000).toFixed(1)}万`
  return n.toLocaleString()
}

export function formatDate(ts: string | null | undefined): string {
  if (!ts) return "—"
  return ts.slice(0, 10)
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
