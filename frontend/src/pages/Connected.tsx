import { getConnected } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import { CheckCircle, XCircle, Plug, AlertTriangle } from 'lucide-react'

export function Connected() {
  const { data, loading, error } = useApi(getConnected, [])

  return (
    <div>
      <PageHeader
        title="Connected Services"
        subtitle="Live Replit integration status"
        actions={data && (
          <div style={{ display: 'flex', align: 'center', gap: 8, fontSize: 13, color: 'var(--text-secondary)' }}>
            <span style={{ fontWeight: 700, color: '#34d399' }}>{data.connected_count}</span> / {data.total_count} connected
          </div>
        )}
      />

      {!data?.proxy_available && (
        <Alert type="warn">
          <AlertTriangle size={14} /> Replit connector proxy not available — status may be inaccurate.
        </Alert>
      )}

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && (
        data.rows.length === 0
          ? <Empty message="No connector services configured." icon={<Plug size={32} />} />
          : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
              {data.rows.map(row => (
                <Card key={row.connector} style={{ padding: '16px 18px' }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                    <div style={{
                      width: 36, height: 36, borderRadius: 8, flexShrink: 0,
                      background: row.connected ? 'rgba(52,211,153,0.1)' : 'rgba(255,255,255,0.04)',
                      border: `1px solid ${row.connected ? 'rgba(52,211,153,0.25)' : 'var(--border)'}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      {row.connected
                        ? <CheckCircle size={16} style={{ color: '#34d399' }} />
                        : <XCircle size={16} style={{ color: 'var(--text-muted)' }} />}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 2 }}>{row.name}</p>
                      <p style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{row.category}</p>
                      {row.description && <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>{row.description}</p>}
                      {row.connected && row.created_at && (
                        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>Since {fmtDate(row.created_at)}</p>
                      )}
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )
      )}
    </div>
  )
}
