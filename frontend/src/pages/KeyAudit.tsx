import { Link } from 'react-router-dom'
import { getKeyAudit } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
import { fmtDate } from '@/lib/utils'
import { ArrowLeft, FileText, Shield, Eye, RefreshCw } from 'lucide-react'

function actionBadge(action: string) {
  const map: Record<string, string> = {
    create: 'badge-green', rotate: 'badge-blue', delete: 'badge-red',
    enable: 'badge-green', disable: 'badge-gray', reveal: 'badge-yellow', reveal_failed: 'badge-red',
  }
  return map[action] || 'badge-gray'
}

export function KeyAudit() {
  const { data, loading, error } = useApi(getKeyAudit, [])

  const creates = data?.entries.filter(e => e.action === 'create').length ?? 0
  const reveals = data?.entries.filter(e => e.action === 'reveal').length ?? 0
  const rotates = data?.entries.filter(e => e.action === 'rotate').length ?? 0

  return (
    <div className="animate-fade-in">
      <Link to="/keys" className="btn btn-ghost btn-sm" style={{ marginBottom: 16, paddingLeft: 0 }}>
        <ArrowLeft size={14} /> Back to vault
      </Link>
      <PageHeader title="Key Audit Log" subtitle="All credential vault actions" />

      {data && data.entries.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 24 }}>
          <GlassStatCard value={data.entries.length} label="Total events" icon={<FileText size={16} />} />
          <GlassStatCard value={creates} label="Created" icon={<Shield size={16} />} glow={creates > 0} />
          <GlassStatCard value={reveals} label="Revealed" icon={<Eye size={16} />} />
          <GlassStatCard value={rotates} label="Rotated" icon={<RefreshCw size={16} />} />
        </div>
      )}

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && (
        data.entries.length === 0
          ? <Empty message="No audit entries yet." icon={<FileText size={32} />} />
          : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead>
                  <tr><th>Provider</th><th>Label</th><th>Action</th><th>User</th><th>Detail</th><th>Time</th></tr>
                </thead>
                <tbody>
                  {data.entries.map((e, i) => (
                    <tr key={i}>
                      <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{e.provider}</td>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{e.label || '—'}</td>
                      <td><span className={`badge ${actionBadge(e.action)}`}>{e.action}</span></td>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{e.actor || '—'}</td>
                      <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{e.detail}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{fmtDate(e.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
      )}
    </div>
  )
}
