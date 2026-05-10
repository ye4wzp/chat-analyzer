/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useState, type ReactNode } from "react"

interface DetailPanelCtx {
  content: ReactNode | null
  open: (node: ReactNode) => void
  close: () => void
}

const Ctx = createContext<DetailPanelCtx>({ content: null, open: () => {}, close: () => {} })

export function DetailPanelProvider({ children }: { children: ReactNode }) {
  const [content, setContent] = useState<ReactNode | null>(null)
  return (
    <Ctx.Provider value={{ content, open: setContent, close: () => setContent(null) }}>
      {children}
    </Ctx.Provider>
  )
}

export const useDetailPanel = () => useContext(Ctx)
