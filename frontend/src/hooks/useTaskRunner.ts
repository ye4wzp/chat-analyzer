import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, subscribeSSE, type TaskProgress, type TaskState } from "@/lib/api"

const TERMINAL = new Set(["done", "error", "cancelled"])
const FALLBACK_POLL_MS = 3000

export interface TaskRunOptions {
  start: () => Promise<{ task_id: string }>
  taskType: string
  loadingText: string
  onDone?: (task: TaskProgress, taskId: string) => void | Promise<void>
  onError?: (msg: string) => void
}

/**
 * Shared start-task → SSE progress → toast → terminal handling. Single-flight
 * lock per taskType; handles done/error/cancelled and releases the lock on
 * every path (falls back to polling /tasks if the SSE channel dies).
 */
export function useTaskRunner() {
  const lockRef = useRef<Set<string>>(new Set())
  const [running, setRunning] = useState<Set<string>>(new Set())
  // Active per-run teardown fns (SSE unsubscribe + fallback interval). Cleared
  // on unmount so navigating away from a page mid-task doesn't leak an open
  // EventSource (they occupy the browser's ~6 conns/origin) or a poll timer.
  const cleanupsRef = useRef<Set<() => void>>(new Set())
  const mountedRef = useRef(true)

  useEffect(() => () => {
    mountedRef.current = false
    cleanupsRef.current.forEach(fn => fn())
    cleanupsRef.current.clear()
  }, [])

  const setLock = (taskType: string, on: boolean) => {
    if (on) lockRef.current.add(taskType)
    else lockRef.current.delete(taskType)
    if (mountedRef.current) setRunning(new Set(lockRef.current))
  }

  const run = useCallback(async ({ start, taskType, loadingText, onDone, onError }: TaskRunOptions) => {
    if (lockRef.current.has(taskType)) return
    setLock(taskType, true)
    const toastId = toast.loading(loadingText)

    let taskId = ""
    try {
      ({ task_id: taskId } = await start())
    } catch (e) {
      const msg = getErrorMessage(e)
      toast.error(msg, { id: toastId })
      onError?.(msg)
      setLock(taskType, false)
      return
    }

    let pollId: number | undefined
    // References `unsubscribe` (a const assigned below) — only ever invoked from
    // async callbacks, by which point subscribeSSE has returned and assigned it.
    const cleanup = () => {
      unsubscribe()
      if (pollId !== undefined) window.clearInterval(pollId)
      cleanupsRef.current.delete(cleanup)
    }

    const finish = async (data: TaskProgress) => {
      cleanup()
      if (data.status === "done") {
        try {
          await onDone?.(data, taskId)
        } finally {
          toast.success(data.message || "完成", { id: toastId })
        }
      } else if (data.status === "cancelled") {
        toast.info(data.message || "已取消", { id: toastId })
      } else {
        toast.error(data.message || "失败", { id: toastId })
        onError?.(data.message || "失败")
      }
      setLock(taskType, false)
    }

    const unsubscribe = subscribeSSE<TaskProgress>(`/tasks/${taskId}/events`, (data) => {
      if (TERMINAL.has(data.status)) {
        void finish(data)
      } else {
        toast.loading(data.message || data.status, { id: toastId })
      }
    }, () => {
      // SSE dead after retries — poll the task list until terminal.
      pollId = window.setInterval(async () => {
        try {
          const tasks = await fetchAPI<TaskState[]>("/tasks")
          const t = tasks.find(x => x.id === taskId)
          if (!t || TERMINAL.has(t.status)) {
            void finish(t ?? { status: "error", progress: 0, message: "任务状态未知" })
          }
        } catch { /* keep polling */ }
      }, FALLBACK_POLL_MS)
    })
    cleanupsRef.current.add(cleanup)
  }, [])

  return { run, running }
}
