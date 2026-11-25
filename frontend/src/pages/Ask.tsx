import { useEffect, useRef, useState } from "react"
import { BookOpen, Loader2, Send, Sparkles } from "lucide-react"
import { fetchAPI, getErrorMessage, type AskKnowledgeSource, type AskResponse, type AskSource } from "@/lib/api"
import { formatTime } from "@/lib/utils"
import { useDetailPanel } from "@/lib/DetailPanelContext"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ChatScopeControls, type ChatScope } from "@/components/ChatScopeControls"

interface Turn extends AskResponse {
  question: string
}

const EXAMPLES = [
  "群里推荐过哪些好用的工具？",
  "最近有没有聊到值得记的经验或结论？",
  "有人跟我约过什么事还没办？",
]

function SourceDetail({ s }: { s: AskSource }) {
  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs text-muted-foreground mb-1">{s.platform} · {s.chat_name || s.chat_id}</p>
        <p className="text-sm font-medium">{s.sender_name || "?"}</p>
        <p className="text-xs text-muted-foreground">{formatTime(s.timestamp)}</p>
      </div>
      <div className="rounded-lg p-4 text-sm leading-relaxed whitespace-pre-wrap bg-secondary">{s.content}</div>
      {s.context && <div className="space-y-2">
        <p className="text-xs text-muted-foreground">回答所依据的前后文（长消息会截取；边框标记命中消息）</p>
        {s.context.map(m => <div key={m.id} className={`rounded p-3 text-sm ${m.id === s.message_id ? "border border-primary" : "bg-secondary"}`}>
          <p className="text-xs text-muted-foreground">{m.sender_name || "?"} · {formatTime(m.timestamp)}</p>
          <p className="whitespace-pre-wrap break-words">{m.content}</p>
        </div>)}
      </div>}
    </div>
  )
}

function Sources({ sources, knowledge }: { sources: AskSource[]; knowledge: AskKnowledgeSource[] }) {
  const { open } = useDetailPanel()
  if (!sources.length && !knowledge.length) return null
  return (
    <div className="mt-3 space-y-1 border-t border-[var(--color-border)] pt-3">
      <p className="text-xs font-medium text-[var(--color-muted-foreground)]">来源</p>
      {sources.map(s => (
        <button
          key={s.ref}
          onClick={() => open(<SourceDetail s={s} />)}
          className="flex w-full items-baseline gap-2 rounded px-2 py-1 text-left text-xs text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-accent)] hover:text-[var(--color-foreground)]"
        >
          <span className="shrink-0 font-mono">【{s.ref}】</span>
          <span className="shrink-0 font-medium text-[var(--color-foreground)]">{s.chat_name || s.chat_id}</span>
          <span className="truncate">{s.sender_name}: {s.preview}</span>
          <span className="ml-auto shrink-0">{formatTime(s.timestamp)}</span>
        </button>
      ))}
      {knowledge.map(k => (
        <div key={k.ref} className="flex w-full items-baseline gap-2 px-2 py-1 text-xs text-[var(--color-muted-foreground)]">
          <span className="shrink-0 font-mono">【{k.ref}】</span>
          <BookOpen className="h-3 w-3 shrink-0 self-center" />
          <span className="shrink-0 font-medium text-[var(--color-foreground)]">{k.title}</span>
          <span className="truncate">{k.preview}</span>
        </div>
      ))}
    </div>
  )
}

export default function Ask() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [scope, setScope] = useState<ChatScope>({ platform: "", chat_id: "", since: "", until: "" })
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [turns.length, loading])

  const ask = async (question: string) => {
    question = question.trim()
    if (!question || loading) return
    setInput("")
    setError("")
    setLoading(true)
    try {
      const history = turns.slice(-3).map(t => ({ question: t.question, answer: t.answer }))
      const res = await fetchAPI<AskResponse>("/ask", {
        method: "POST",
        body: JSON.stringify({ question, history, ...scope }),
      })
      setTurns(prev => [...prev, { question, ...res }])
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto p-6 md:p-8">
        <div className="mx-auto max-w-3xl space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            <ChatScopeControls value={scope} disabled={loading} onChange={next => { setScope(next); setTurns([]); setError("") }} />
            <p className="w-full text-xs text-muted-foreground">回答依据有限检索片段；切换范围会清空当前问答，避免混入其他会话。</p>
          </div>
          {turns.length === 0 && !loading && (
            <div className="flex flex-col items-center gap-4 pt-16 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--color-primary)] text-[var(--color-primary-foreground)]">
                <Sparkles className="h-6 w-6" />
              </div>
              <div>
                <h1 className="text-lg font-semibold">问聊天记录</h1>
                <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
                  用自然语言检索微信 / QQ / Telegram 的聊天记录和知识库，回答附带原文引用
                </p>
              </div>
              <div className="flex flex-col gap-2">
                {EXAMPLES.map(q => (
                  <button
                    key={q}
                    onClick={() => { void ask(q) }}
                    className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-2 text-sm text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-accent)] hover:text-[var(--color-foreground)]"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t, i) => (
            <div key={i} className="space-y-3">
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-md bg-[var(--color-primary)] px-4 py-2.5 text-sm text-[var(--color-primary-foreground)]">
                  {t.question}
                </div>
              </div>
              <div className="rounded-2xl rounded-bl-md border border-[var(--color-border)] bg-[var(--color-card)] p-4">
                <div className="whitespace-pre-wrap text-sm leading-relaxed">{t.answer}</div>
                <Sources sources={t.sources} knowledge={t.knowledge} />
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
              <Loader2 className="h-4 w-4 animate-spin" />
              检索聊天记录并生成回答...
            </div>
          )}
          {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="border-t border-[var(--color-border)] bg-[var(--color-background)] p-4">
        <div className="mx-auto flex max-w-3xl items-center gap-2">
          <Input
            className="h-11 flex-1"
            placeholder="问点什么，比如：老张上次说的装修报价是多少？"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.nativeEvent.isComposing) void ask(input) }}
            disabled={loading}
          />
          <Button className="h-11" onClick={() => { void ask(input) }} disabled={loading || !input.trim()}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </div>
  )
}
