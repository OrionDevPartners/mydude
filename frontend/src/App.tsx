import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from '@/contexts/AuthContext'
import { Layout } from '@/components/Layout'
import { ErrorPage } from '@/pages/ErrorPage'
import { Spinner } from '@/components/ui'

const Login = lazy(() => import('@/pages/Login').then((m) => ({ default: m.Login })))
const Dashboard = lazy(() => import('@/pages/Dashboard').then((m) => ({ default: m.Dashboard })))
const TaskHistory = lazy(() => import('@/pages/TaskHistory').then((m) => ({ default: m.TaskHistory })))
const TaskDetail = lazy(() => import('@/pages/TaskDetail').then((m) => ({ default: m.TaskDetail })))
const Keys = lazy(() => import('@/pages/Keys').then((m) => ({ default: m.Keys })))
const KeyAudit = lazy(() => import('@/pages/KeyAudit').then((m) => ({ default: m.KeyAudit })))
const AuditLog = lazy(() => import('@/pages/AuditLog').then((m) => ({ default: m.AuditLog })))
const Users = lazy(() => import('@/pages/Users').then((m) => ({ default: m.Users })))
const Directory = lazy(() => import('@/pages/Directory').then((m) => ({ default: m.Directory })))
const Connected = lazy(() => import('@/pages/Connected').then((m) => ({ default: m.Connected })))
const Capabilities = lazy(() => import('@/pages/Capabilities').then((m) => ({ default: m.Capabilities })))
const Governance = lazy(() => import('@/pages/Governance').then((m) => ({ default: m.Governance })))
const Provenance = lazy(() => import('@/pages/Provenance').then((m) => ({ default: m.Provenance })))
const Memory = lazy(() => import('@/pages/Memory').then((m) => ({ default: m.Memory })))
const System = lazy(() => import('@/pages/System').then((m) => ({ default: m.System })))
const LocalModels = lazy(() => import('@/pages/LocalModels').then((m) => ({ default: m.LocalModels })))
const Subscriptions = lazy(() => import('@/pages/Subscriptions').then((m) => ({ default: m.Subscriptions })))
const Finance = lazy(() => import('@/pages/Finance').then((m) => ({ default: m.Finance })))
const Coach = lazy(() => import('@/pages/Coach').then((m) => ({ default: m.Coach })))
const Avatar = lazy(() => import('@/pages/Avatar').then((m) => ({ default: m.Avatar })))
const Fleet = lazy(() => import('@/pages/Fleet').then((m) => ({ default: m.Fleet })))
const PromptEngine = lazy(() => import('@/pages/PromptEngine').then((m) => ({ default: m.PromptEngine })))
const Evolution = lazy(() => import('@/pages/Evolution').then((m) => ({ default: m.Evolution })))

function PageFallback() {
  return (
    <div style={{ minHeight: '60vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <Spinner size={28} />
    </div>
  )
}

function AuthGuard({ children }: { children: React.ReactNode }) {
  const { authenticated } = useAuth()
  if (authenticated === null) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spinner size={28} />
      </div>
    )
  }
  if (!authenticated) return <Navigate to="/login" replace />
  return <>{children}</>
}

function AppRoutes() {
  const { authenticated } = useAuth()

  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route path="/login" element={
          authenticated === true ? <Navigate to="/" replace /> : <Login />
        } />
        <Route path="/*" element={
          <AuthGuard>
            <Layout>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/history" element={<TaskHistory />} />
                <Route path="/tasks/:id" element={<TaskDetail />} />
                <Route path="/keys" element={<Keys />} />
                <Route path="/keys/audit" element={<KeyAudit />} />
                <Route path="/audit" element={<AuditLog />} />
                <Route path="/users" element={<Users />} />
                <Route path="/directory" element={<Directory />} />
                <Route path="/connected" element={<Connected />} />
                <Route path="/capabilities" element={<Capabilities />} />
                <Route path="/governance" element={<Governance />} />
                <Route path="/provenance" element={<Provenance />} />
                <Route path="/memory" element={<Memory />} />
                <Route path="/system" element={<System />} />
                <Route path="/local-models" element={<LocalModels />} />
                <Route path="/subscriptions" element={<Subscriptions />} />
                <Route path="/finance" element={<Finance />} />
                <Route path="/coach" element={<Coach />} />
                <Route path="/avatar" element={<Avatar />} />
                <Route path="/fleet" element={<Fleet />} />
                <Route path="/prompt-engine" element={<PromptEngine />} />
                <Route path="/evolution" element={<Evolution />} />
                <Route path="*" element={<ErrorPage code={404} message="Page not found" />} />
              </Routes>
            </Layout>
          </AuthGuard>
        } />
      </Routes>
    </Suspense>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  )
}
