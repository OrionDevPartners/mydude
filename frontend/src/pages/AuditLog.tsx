import { getAuditLog } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
import { fmtDate } from '@/lib/utils'
import { ScrollText, CheckCircle, AlertTriangle, Terminal } from 'lucide-react'

function statusBadge(status: string) {
  const s = status.toLowerCase()
  if (s === 'ok' || s === 'success') return 'badge-green'
  if (s === 'error' || s === 'failed' || s === 'fail') return 'badge-red'
  if (s === 'warn' || s === 'warning') return 'badge-yellow'
  return 'badge-gray'
}

export function AuditLog() {
  const { data, loading, error } = useApi(getAuditLog, [])

  const entries = data?.entries ?? []
  const ok = entries.filter(e => ['ok', 'success'].includes(e.status.toLowerCase())).length
  const errors = entries.filter(e => ['error', 'failed', 'fail'].includes(e.status.toLowerCase())).length

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="System Audit Log"
        subtitle="Every governance control action — recorded for accountability"
      />

      {entries.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 24 }}>
          <GlassStatCard value={entries.length} label="Total events" icon={<ScrollText size={16} />} />
          <GlassStatCard value={ok} label="Succeeded" icon={<CheckCircle size={16} />} glow={ok > 0} />
          <GlassStatCard value={errors} label="Errors" icon={<AlertTriangle size={16} />} glow={errors > 0} />
        </div>
      )}

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && (
        entries.length === 0
          ? <Empty message="No system actions recorded yet." icon={<ScrollText size={32} />} />
          : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead>
                  <tr><th>Action</th><th>User</th><th>Status</th><th>Details</th><th>Time</th></tr>
                </thead>
                <tbody>
                  {entries.map(e => (
                    <tr key={e.id}>
                      <td style={{ fontFamily: 'monospace', fontSize: 12, whiteSpace: 'nowrap' }}>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                          <Terminal size={12} style={{ opacity: 0.5 }} /> {e.command}
                        </span>
                      </td>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{e.user}</td>
                      <td><span className={`badge ${statusBadge(e.status)}`}>{e.status}</span></td>
                      <td style={{ fontSize: 12, color: 'var(--text-muted)', maxWidth: 360 }}>
                        {e.output_preview || e.args || '—'}
                      </td>
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
