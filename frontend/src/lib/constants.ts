export const PAGE_SIZE = 50

export const MASKED = "********"

export const PLATFORM_COLOR: Record<string, string> = {
  wechat: "#22c55e",
  qq: "#38bdf8",
  telegram: "#818cf8",
}

export const PLATFORM_LABEL: Record<string, string> = {
  wechat: "微信",
  qq: "QQ",
  telegram: "Telegram",
}

// Cross-platform message kinds (backend services/stats.py KINDS). Display
// order matters for legends; colors are deliberately distinct hues so the
// donut stays readable with 15+ slices.
export const KIND_LABEL: Record<string, string> = {
  text: "文本", image: "图片", quote: "引用", system: "系统", sticker: "表情",
  miniprogram: "小程序", link: "链接", share: "分享", file: "文件", video: "视频",
  voice: "语音", call: "通话", location: "位置", card: "名片", redpacket: "红包",
  transfer: "转账", media: "媒体", other: "其他",
}

export const KIND_COLOR: Record<string, string> = {
  text: "#22c55e", image: "#3b82f6", quote: "#f97316", system: "#ef4444", sticker: "#a855f7",
  miniprogram: "#14b8a6", link: "#eab308", share: "#84cc16", file: "#0ea5e9", video: "#6366f1",
  voice: "#f59e0b", call: "#ec4899", location: "#10b981", card: "#8b5cf6", redpacket: "#dc2626",
  transfer: "#f43f5e", media: "#64748b", other: "#94a3b8",
}

export const WEEKDAY_LABEL = ["日", "一", "二", "三", "四", "五", "六"]

export const CHAT_TYPE_LABEL: Record<string, string> = {
  group: "群",
  private: "私聊",
  channel: "频道",
}

export function chatTypeLabel(type: string | null | undefined): string {
  return (type && CHAT_TYPE_LABEL[type]) || "聊天"
}
