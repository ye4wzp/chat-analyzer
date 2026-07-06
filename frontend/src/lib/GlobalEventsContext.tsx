/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { subscribeGlobalEvents, type GlobalEventsData } from "@/lib/api"

const GlobalEventsContext = createContext<GlobalEventsData>({ tasks: [], unread: 0 })

export function GlobalEventsProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GlobalEventsData>({ tasks: [], unread: 0 })
  useEffect(() => subscribeGlobalEvents(setState), [])
  return <GlobalEventsContext.Provider value={state}>{children}</GlobalEventsContext.Provider>
}

export function useGlobalEvents() {
  return useContext(GlobalEventsContext)
}
