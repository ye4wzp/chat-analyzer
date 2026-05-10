import { useEffect, useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Loader2, Download, Trash2, Database, RefreshCw } from "lucide-react"
import { formatTime } from "@/lib/utils"

interface Backup {
  name: string
  size: number
  size_human: string
  created_at: string
}

export function BackupsPanel() {
  const [items, setItems] = useState<Backup[] | null>(null)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = async () => {
    try {
      setItems(await fetchAPI<Backup[]>("/backups"))
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  useEffect(() => { void refresh() }, [])

  const create = async () => {
    setCreating(true)
    const id = toast.loading("备份中...（VACUUM + gzip 一般 2-15 秒）")
    try {
      const info = await fetchAPI<Backup>("/backups", { method: "POST" })
      toast.success(`已备份 ${info.size_human}`, { id })
      await refresh()
    } catch (e) {
      toast.error(getErrorMessage(e), { id })
    } finally {
      setCreating(false)
    }
  }

  const remove = async (name: string) => {
    if (!confirm(`确定删除备份 ${name}？`)) return
    setBusy(name)
    try {
      await fetchAPI(`/backups/${encodeURIComponent(name)}`, { method: "DELETE" })
      toast.success("已删除")
      await refresh()
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  const totalSize = items?.reduce((s, b) => s + b.size, 0) ?? 0

  return (
    <section className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold flex items-center gap-2">
            <Database className="h-4 w-4 text-[var(--color-info)]" />
            数据库备份
          </h3>
          <p className="text-xs text-[var(--color-muted-foreground)] mt-1 leading-relaxed">
            每次同步前自动快照 chat.db（gzip 压缩，最多保留 5 份）。手动还原：
            <code className="font-mono mx-1">gunzip -c xxx.bak.gz &gt; ~/.chat-analyzer/data/chat.db</code>
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button variant="outline" size="sm" onClick={refresh} disabled={creating}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />刷新
          </Button>
          <Button size="sm" onClick={create} disabled={creating}>
            {creating ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Download className="mr-1.5 h-3.5 w-3.5" />}
            立即备份
          </Button>
        </div>
      </div>

      {items === null ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-md border border-dashed border-[var(--color-border)] p-6 text-center text-sm text-[var(--color-muted-foreground)]">
          暂无备份。点"立即备份"创建第一个，或下次同步会自动触发。
        </div>
      ) : (
        <>
          <div className="rounded-lg border border-[var(--color-border)] overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-[var(--color-secondary)] text-[var(--color-muted-foreground)]">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">文件</th>
                  <th className="text-right px-4 py-2 font-medium tabular-nums">大小</th>
                  <th className="text-right px-4 py-2 font-medium">创建时间</th>
                  <th className="w-10" />
                </tr>
              </thead>
              <tbody>
                {items.map(b => (
                  <tr key={b.name} className="border-t border-[var(--color-border)]/50">
                    <td className="px-4 py-2 font-mono text-xs">{b.name}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-[var(--color-muted-foreground)]">{b.size_human}</td>
                    <td className="px-4 py-2 text-right text-xs text-[var(--color-muted-foreground)]">{formatTime(b.created_at)}</td>
                    <td className="px-2 py-2">
                      <button
                        onClick={() => remove(b.name)}
                        disabled={busy === b.name}
                        title="删除"
                        className="rounded p-1 text-[var(--color-muted-foreground)] hover:bg-[var(--color-destructive)]/10 hover:text-[var(--color-destructive)] disabled:opacity-50"
                      >
                        {busy === b.name ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-[var(--color-muted-foreground)] tabular-nums">
            合计 {items.length} 份 · {(totalSize / 1024 / 1024).toFixed(1)}MB
          </p>
        </>
      )}
    </section>
  )
}
