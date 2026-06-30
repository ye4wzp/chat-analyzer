import { useCallback, useRef, useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, subscribeSSE, type PendingKnowledge, type TaskProgress } from "@/lib/api"

/**
 * Shared logic for triggering an AI analysis run and surfacing the review
 * modal when it completes. Used by both the global "Run Analysis" button in
 * Layout and per-chat "Analyze this chat" buttons in pages like Chats.
 *
 * Usage:
 *   const { running, run, modalProps } = useAnalyzeRunner()
 *   <Button onClick={() => run({ chats: ["..."] })} disabled={running}>...</Button>
 *   {modalProps.results && <KnowledgeReviewModal {...modalProps} />}
 */
export interface AnalyzeRunOptions {
  chat?: string
  chats?: string[]
  platform?: string
  chat_id?: string
  since?: string
  until?: string
  limit?: number
  full?: boolean
}

export function useAnalyzeRunner() {
  const [running, setRunning] = useState(false)
  const [results, setResults] = useState<PendingKnowledge[] | null>(null)
  const [taskId, setTaskId] = useState("")
  const [summary, setSummary] = useState("")
  const esRef = useRef<EventSource | null>(null)

  const run = useCallback(async (opts: AnalyzeRunOptions = {}) => {
    if (running) return
    setRunning(true)
    const toastId = toast.loading("AI 分析启动中...")
    try {
      const { task_id } = await fetchAPI<{ task_id: string }>("/analyze", {
        method: "POST",
        body: JSON.stringify(opts),
      })
      esRef.current?.close()
      esRef.current = subscribeSSE<TaskProgress>(`/tasks/${task_id}/events`, async (data) => {
        toast.loading(data.message || data.status, { id: toastId })
        if (data.status === "done") {
          esRef.current?.close()
          try {
            const r = await fetchAPI<PendingKnowledge[]>(`/analyze/${task_id}/results`)
            setResults(r)
            setTaskId(task_id)
            setSummary(data.summary || "")
          } catch { /* ignore — error toast already up */ }
          toast.success(data.message || "完成", { id: toastId })
          setRunning(false)
        } else if (data.status === "error") {
          esRef.current?.close()
          toast.error(data.message || "失败", { id: toastId })
          setRunning(false)
        } else if (data.status === "cancelled") {
          esRef.current?.close()
          toast.info(data.message || "已取消", { id: toastId })
          setRunning(false)
        }
      }, () => {
        toast.dismiss(toastId)
        setRunning(false)
      })
    } catch (e: unknown) {
      toast.error(getErrorMessage(e), { id: toastId })
      setRunning(false)
    }
  }, [running])

  const closeModal = useCallback(() => setResults(null), [])

  return {
    running,
    run,
    modalProps: { results, taskId, summary, onClose: closeModal, onSaved: closeModal },
  }
}
