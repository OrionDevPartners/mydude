import { Link } from 'react-router-dom'
import { AlertCircle } from 'lucide-react'

export function ErrorPage({ code = 404, message = 'Page not found' }: { code?: number; message?: string }) {
  return (
    <div style={{ minHeight: '60vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: 40 }}>
      <div style={{ width: 64, height: 64, borderRadius: 16, background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px' }}>
        <AlertCircle size={28} style={{ color: '#f87171' }} />
      </div>
      <h1 style={{ fontSize: 52, fontWeight: 800, color: 'var(--text-primary)', margin: '0 0 8px', lineHeight: 1 }}>{code}</h1>
      <p style={{ fontSize: 16, color: 'var(--text-secondary)', marginBottom: 28 }}>{message}</p>
      <Link to="/" className="btn btn-primary">Go to dashboard</Link>
    </div>
  )
}
