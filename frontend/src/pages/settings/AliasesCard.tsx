import { useEffect, useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, type AliasGroup } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { Loader2, Plus, Trash2, UserRound } from "lucide-react"
import { PLATFORM_LABEL } from "@/lib/constants"

export function AliasesCard() {
  const [groups, setGroups] = useState<AliasGroup[] | null>(null)
  const [canonical, setCanonical] = useState("")
  const [platform, setPlatform] = useState("wechat")
  const [platformId, setPlatformId] = useState("")
  const [platformName, setPlatformName] = useState("")
  const [busy, setBusy] = useState(false)

  const refresh = async () => {
    try {
      setGroups(await fetchAPI<AliasGroup[]>("/aliases"))
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  useEffect(() => { void refresh() }, [])

  const add = async () => {
    if (!canonical.trim() || !platformId.trim()) return
    setBusy(true)
    try {
      await fetchAPI("/aliases", {
        method: "POST",
        body: JSON.stringify({
          canonical_name: canonical.trim(),
          platform,
          platform_id: platformId.trim(),
          platform_name: platformName.trim() || null,
        }),
      })
      setPlatformId("")
      setPlatformName("")
      toast.success("已添加身份映射")
      await refresh()
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id: number) => {
    try {
      await fetchAPI(`/aliases/${id}`, { method: "DELETE" })
      await refresh()
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <UserRound className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
        身份合并
      </h3>
      <p className="text-xs text-[var(--color-muted-foreground)] mt-1 leading-relaxed">
        把同一个人在不同平台的账号映射到统一姓名，VIP 白名单和预过滤会跨平台生效。
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Input value={canonical} onChange={e => setCanonical(e.target.value)} placeholder="统一姓名" className="w-32" />
        <Select value={platform} onChange={e => setPlatform(e.target.value)} className="w-28">
          <option value="wechat">微信</option>
          <option value="qq">QQ</option>
          <option value="telegram">Telegram</option>
        </Select>
        <Input value={platformId} onChange={e => setPlatformId(e.target.value)} placeholder="平台 ID" className="w-40" />
        <Input value={platformName} onChange={e => setPlatformName(e.target.value)} placeholder="平台昵称（可选）" className="w-40" />
        <Button size="sm" onClick={() => void add()} disabled={busy || !canonical.trim() || !platformId.trim()}>
          {busy ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Plus className="mr-1 h-3.5 w-3.5" />}添加
        </Button>
      </div>

      {groups === null ? (
        <div className="mt-4 flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />加载中...
        </div>
      ) : groups.length === 0 ? (
        <p className="mt-4 text-xs text-[var(--color-muted-foreground)]">暂无身份映射。</p>
      ) : (
        <div className="mt-4 space-y-3">
          {groups.map(g => (
            <div key={g.canonical_name} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card-elevated)] p-3">
              <p className="text-sm font-medium">{g.canonical_name}</p>
              <div className="mt-1.5 space-y-1">
                {g.entries.map(e => (
                  <div key={e.id} className="flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
                    <span className="w-16 shrink-0">{PLATFORM_LABEL[e.platform] || e.platform}</span>
                    <span className="font-mono">{e.platform_id}</span>
                    {e.platform_name && <span className="truncate">({e.platform_name})</span>}
                    <button
                      onClick={() => void remove(e.id)}
                      aria-label="删除映射"
                      className="ml-auto rounded p-1 hover:bg-[var(--color-destructive)]/10 hover:text-[var(--color-destructive)]"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
