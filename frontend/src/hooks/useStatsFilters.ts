import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { fetchAPI, type StatsAccount } from "@/lib/api"
import { useGlobalEvents } from "@/lib/GlobalEventsContext"

/**
 * URL-synced platform / account filters shared by every stats page, plus the
 * list of known accounts (for the 主号/多号 switch). Returns a query-string
 * fragment ready to append to /api/stats/* calls.
 */
export function useStatsFilters() {
  const [params, setParams] = useSearchParams()
  const platform = params.get("platform") || ""
  const account = params.get("account") || ""
  const [accounts, setAccounts] = useState<StatsAccount[]>([])

  useEffect(() => {
    fetchAPI<StatsAccount[]>("/stats/accounts").then(setAccounts).catch(() => setAccounts([]))
  }, [])

  const set = useCallback((key: "platform" | "account", value: string) => {
    setParams(prev => {
      const next = new URLSearchParams(prev)
      if (value) next.set(key, value); else next.delete(key)
      // Switching platform invalidates the account choice.
      if (key === "platform") next.delete("account")
      return next
    }, { replace: true })
  }, [setParams])

  const query = useMemo(() => {
    const q = new URLSearchParams()
    if (platform) q.set("platform", platform)
    if (account) q.set("account", account)
    const s = q.toString()
    return s ? `&${s}` : ""
  }, [platform, account])

  // Accounts relevant to the chosen platform; the switch only renders when a
  // platform has more than one account (or accounts are named at all).
  const platformAccounts = useMemo(() => {
    const list = platform ? accounts.filter(a => a.platform === platform) : accounts
    return list.filter(a => a.account)
  }, [accounts, platform])
  const hasMultiAccount = useMemo(() => {
    const byPlatform = new Map<string, Set<string>>()
    for (const a of accounts) {
      if (!byPlatform.has(a.platform)) byPlatform.set(a.platform, new Set())
      byPlatform.get(a.platform)!.add(a.account)
    }
    return Array.from(byPlatform.values()).some(s => s.size > 1)
  }, [accounts])

  return { platform, account, setPlatform: (v: string) => set("platform", v), setAccount: (v: string) => set("account", v), query, accounts, platformAccounts, hasMultiAccount }
}

/** Run `cb` each time a background stats refresh finishes, so pages built on
 *  the rollups pick up new data without a manual reload. */
export function useOnStatsDone(cb: () => void) {
  const { tasks } = useGlobalEvents()
  const doneKey = tasks.filter(t => t.type === "stats" && t.status === "done").map(t => t.id).join(",")
  const seen = useRef(doneKey)
  useEffect(() => {
    if (doneKey && doneKey !== seen.current) {
      seen.current = doneKey
      cb()
    }
  }, [doneKey, cb])
}
