import { useCallback, useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, type PendingKnowledge } from "@/lib/api"
import { useTaskRunner } from "@/hooks/useTaskRunner"

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
  const { run: runTask, running } = useTaskRunner()
  const [results, setResults] = useState<PendingKnowledge[] | null>(null)
  const [taskId, setTaskId] = useState("")
  const [summary, setSummary] = useState("")

  const run = useCallback((opts: AnalyzeRunOptions = {}) => runTask({
    start: () => fetchAPI<{ task_id: string }>("/analyze", {
      method: "POST",
      body: JSON.stringify(opts),
    }),
    taskType: "analyze",
    loadingText: "AI 分析启动中...",
    onDone: async (task, id) => {
      try {
        const r = await fetchAPI<PendingKnowledge[]>(`/analyze/${id}/results`)
        setResults(r)
        setTaskId(id)
        setSummary(task.summary || "")
      } catch (e) {
        // The run finished but its results couldn't be fetched (batch expired /
        // discarded). Surface it — otherwise the success toast fires with no
        // review modal and no explanation.
        toast.error(`分析已完成但结果获取失败：${getErrorMessage(e)}`)
      }
    },
  }), [runTask])

  const closeModal = useCallback(() => setResults(null), [])

  return {
    running: running.has("analyze"),
    run,
    modalProps: { results, taskId, summary, onClose: closeModal, onSaved: closeModal },
  }
}
