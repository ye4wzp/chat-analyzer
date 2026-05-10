import { useState } from "react"
import { fetchAPI, getErrorMessage, type LLMModelsResponse } from "@/lib/api"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { CheckCircle, AlertTriangle } from "lucide-react"

interface Props {
  provider: string
  url: string
  model: string
  apiKey: string
  embeddingModel: string
  setProvider: (v: string) => void
  setUrl: (v: string) => void
  setModel: (v: string) => void
  setApiKey: (v: string) => void
  setEmbeddingModel: (v: string) => void
  onError: (msg: string) => void
}

export function LLMTab({ provider, url, model, apiKey, embeddingModel, setProvider, setUrl, setModel, setApiKey, setEmbeddingModel, onError }: Props) {
  const [models, setModels] = useState<string[]>([])
  const [testResult, setTestResult] = useState<"idle" | "ok" | "fail">("idle")

  const fetchModels = async () => {
    try {
      // Save config first so backend uses the latest URL/key
      await fetchAPI("/config", {
        method: "PUT",
        body: JSON.stringify({
          llm_provider: provider,
          llm_api_url: url,
          llm_api_key: apiKey === "********" ? undefined : apiKey,
        }),
      })
      const res = await fetchAPI<LLMModelsResponse>("/llm/models")
      setModels(res.models)
      if (res.models.length > 0 && !model) setModel(res.models[0])
    } catch (e: unknown) {
      setModels([])
      onError(getErrorMessage(e))
    }
  }

  const testConnection = async () => {
    setTestResult("idle")
    try {
      await fetchAPI<{ status: string }>("/llm/test")
      setTestResult("ok")
    } catch {
      setTestResult("fail")
    }
  }

  return (
    <Card>
      <CardHeader><CardTitle>AI 模型配置</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <div className="max-w-md space-y-1">
          <label className="text-xs text-muted-foreground">LLM 提供商</label>
          <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="claude_cli">Claude Code CLI (claude -p)</option>
            <option value="openai_compatible">OpenAI 兼容 API (LM Studio / Ollama)</option>
          </Select>
        </div>

        {provider === "openai_compatible" && (
          <>
            <div className="max-w-md space-y-1">
              <label className="text-xs text-muted-foreground">API 地址</label>
              <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://127.0.0.1:1234/v1" />
            </div>
            <div className="max-w-md space-y-1">
              <label className="text-xs text-muted-foreground">API Key</label>
              <Input value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="lm-studio" />
            </div>
            <div className="max-w-md space-y-1">
              <label className="text-xs text-muted-foreground">模型</label>
              <div className="flex gap-2">
                <Select value={model} onChange={(e) => setModel(e.target.value)} className="flex-1">
                  {models.length > 0
                    ? models.map((m) => <option key={m} value={m}>{m}</option>)
                    : <option value="">点击"获取模型"加载</option>}
                </Select>
                <Button variant="outline" size="sm" onClick={fetchModels}>获取模型</Button>
              </div>
              {models.length === 0 && (
                <Input className="mt-2" value={model} onChange={(e) => setModel(e.target.value)} placeholder="或手动输入模型名" />
              )}
            </div>
            <div className="max-w-md space-y-1">
              <label className="text-xs text-muted-foreground">
                Embedding 模型（用于语义搜索 / 关联推荐，留空则禁用）
              </label>
              <Select value={embeddingModel} onChange={(e) => setEmbeddingModel(e.target.value)}>
                <option value="">（不使用）</option>
                {/* Filter for likely embedding models — most providers tag them. */}
                {models.filter(m => /embed|nomic|bge|e5|gte/i.test(m)).map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
                {/* Fallback: also let user pick any model name in case our filter misses */}
                {!models.filter(m => /embed|nomic|bge|e5|gte/i.test(m)).includes(embeddingModel) && embeddingModel && (
                  <option value={embeddingModel}>{embeddingModel}</option>
                )}
              </Select>
              <Input className="mt-2" value={embeddingModel} onChange={(e) => setEmbeddingModel(e.target.value)} placeholder="或手动输入，如 text-embedding-nomic-embed-text-v1.5" />
            </div>
            <div className="flex items-center gap-3">
              <Button variant="outline" size="sm" onClick={testConnection}>测试连接</Button>
              {testResult === "ok" && <span className="flex items-center gap-1 text-sm text-green-400"><CheckCircle className="h-4 w-4" /> 连接成功</span>}
              {testResult === "fail" && <span className="flex items-center gap-1 text-sm text-destructive"><AlertTriangle className="h-4 w-4" /> 连接失败</span>}
            </div>
          </>
        )}

        {provider === "claude_cli" && (
          <p className="text-xs text-muted-foreground">
            使用本地已认证的 Claude Code CLI，无需额外配置。确保 <code className="text-primary">claude</code> 命令可用。
          </p>
        )}
      </CardContent>
    </Card>
  )
}
