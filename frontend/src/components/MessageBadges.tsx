import { Badge } from "@/components/ui/badge"

export function UrgencyBadge({ urgency }: { urgency?: number }) {
  if (!urgency) return null
  const variant = urgency >= 4 ? "destructive" : urgency >= 2 ? "secondary" : "outline"
  return <Badge variant={variant}>{urgency}</Badge>
}

const CATEGORY_MAP: Record<string, { label: string; variant: "default" | "destructive" | "secondary" | "outline" }> = {
  important: { label: "重要", variant: "default" },
  todo: { label: "待办", variant: "destructive" },
  casual: { label: "闲聊", variant: "secondary" },
  pending: { label: "待处理", variant: "outline" },
}

export function CategoryBadge({ category }: { category?: string }) {
  if (!category) return null
  const info = CATEGORY_MAP[category]
  if (!info) return <Badge variant="outline">{category}</Badge>
  return <Badge variant={info.variant}>{info.label}</Badge>
}
