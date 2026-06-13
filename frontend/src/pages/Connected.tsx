import { getConnected } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
import { fmtDate } from '@/lib/utils'
import { CheckCircle, XCircle, Plug, AlertTriangle, Link2 } from 'lucide-react'

export function Connected() {
  const { data, loading, error } = useApi(getConnected, [])

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Connected Services"
        subtitle="Live Replit integration status"
      />

      {!data?.proxy_available && (
        <Alert type="warn">
          <AlertTriangle size={14} /> Replit connector proxy not available — status may be inaccurate.
        </Alert>
      )}

      {data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 24 }}>
          <GlassStatCard
            value={data.connected_count}
            label="Connected"
            icon={<CheckCircle size={16} />}
            glow={data.connected_count > 0}
          />
          <GlassStatCard
            value={data.total_count - data.connected_count}
            label="Disconnected"
            icon={<XCircle size={16} />}
          />
          <GlassStatCard
            value={data.total_count}
            label="Total services"
            icon={<Link2 size={16} />}
          />
        </div>
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
                      width: 38, height: 38, borderRadius: 10, flexShrink: 0,
                      background: row.connected ? 'rgba(52,211,153,0.1)' : 'rgba(255,255,255,0.04)',
                      border: `1px solid ${row.connected ? 'rgba(52,211,153,0.25)' : 'var(--border)'}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      {row.connected
                        ? <CheckCircle size={17} style={{ color: '#34d399' }} />
                        : <XCircle size={17} style={{ color: 'var(--text-muted)' }} />}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 2 }}>{row.name}</p>
                      <p style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{row.category}</p>
                      {row.description && <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.4 }}>{row.description}</p>}
                      {row.connected && row.created_at && (
                        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>Connected {fmtDate(row.created_at)}</p>
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
