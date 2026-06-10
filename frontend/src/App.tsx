import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from '@/contexts/AuthContext'
import { Layout } from '@/components/Layout'
import { Login } from '@/pages/Login'
import { Dashboard } from '@/pages/Dashboard'
import { TaskHistory } from '@/pages/TaskHistory'
import { TaskDetail } from '@/pages/TaskDetail'
import { Keys } from '@/pages/Keys'
import { KeyAudit } from '@/pages/KeyAudit'
import { Directory } from '@/pages/Directory'
import { Connected } from '@/pages/Connected'
import { Capabilities } from '@/pages/Capabilities'
import { Governance } from '@/pages/Governance'
import { Provenance } from '@/pages/Provenance'
import { Memory } from '@/pages/Memory'
import { System } from '@/pages/System'
import { LocalModels } from '@/pages/LocalModels'
import { Subscriptions } from '@/pages/Subscriptions'
import { Finance } from '@/pages/Finance'
import { ErrorPage } from '@/pages/ErrorPage'
import { Spinner } from '@/components/ui'

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
              <Route path="*" element={<ErrorPage code={404} message="Page not found" />} />
            </Routes>
          </Layout>
        </AuthGuard>
      } />
    </Routes>
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
