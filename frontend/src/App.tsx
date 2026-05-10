import { BrowserRouter, Routes, Route } from "react-router-dom"
import { lazy, Suspense } from "react"
import Layout from "@/pages/Layout"
import { ErrorBoundary } from "@/components/ErrorBoundary"

const Dashboard = lazy(() => import("@/pages/Dashboard"))
const Chats = lazy(() => import("@/pages/Chats"))
const Timeline = lazy(() => import("@/pages/Timeline"))
const Search = lazy(() => import("@/pages/Search"))
const Knowledge = lazy(() => import("@/pages/Knowledge"))
const Inbox = lazy(() => import("@/pages/Inbox"))
const Settings = lazy(() => import("@/pages/Settings"))

function PageFallback() {
  return (
    <div className="flex items-center justify-center h-64 text-sm text-muted-foreground">
      加载中...
    </div>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Suspense fallback={<PageFallback />}><Dashboard /></Suspense>} />
            <Route path="chats" element={<Suspense fallback={<PageFallback />}><Chats /></Suspense>} />
            <Route path="timeline" element={<Suspense fallback={<PageFallback />}><Timeline /></Suspense>} />
            <Route path="search" element={<Suspense fallback={<PageFallback />}><Search /></Suspense>} />
            <Route path="knowledge" element={<Suspense fallback={<PageFallback />}><Knowledge /></Suspense>} />
            <Route path="inbox" element={<Suspense fallback={<PageFallback />}><Inbox /></Suspense>} />
            <Route path="settings" element={<Suspense fallback={<PageFallback />}><Settings /></Suspense>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  )
}
