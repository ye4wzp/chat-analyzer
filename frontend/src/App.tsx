import { BrowserRouter, Routes, Route } from "react-router-dom"
import Layout from "@/pages/Layout"
import Dashboard from "@/pages/Dashboard"
import Chats from "@/pages/Chats"
import Timeline from "@/pages/Timeline"
import Search from "@/pages/Search"
import Knowledge from "@/pages/Knowledge"
import Settings from "@/pages/Settings"
import { ErrorBoundary } from "@/components/ErrorBoundary"

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="chats" element={<Chats />} />
            <Route path="timeline" element={<Timeline />} />
            <Route path="search" element={<Search />} />
            <Route path="knowledge" element={<Knowledge />} />
            <Route path="settings" element={<Settings />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  )
}
