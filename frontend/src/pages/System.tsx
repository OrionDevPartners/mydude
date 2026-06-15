import { getSystem } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { GlassStatCard, GlassSection } from '@/components/glass'
import { Activity, CheckCircle, XCircle, AlertTriangle, RefreshCw } from 'lucide-react'

export function System() {
  const { data, loading, error, refetch } = useApi(getSystem, [])

  function statusKind(status: unknown): 'ok' | 'warn' | 'bad' {
    const s = String(status).toLowerCase()
    if (s === 'ok' || s === 'healthy' || s === 'true') return 'ok'
    if (s === 'degraded' || s === 'warn') return 'warn'
    return 'bad'
  }

  function statusIcon(status: unknown) {
    const k = statusKind(status)
    if (k === 'ok') return <CheckCircle size={16} style={{ color: '#34d399' }} />
    if (k === 'warn') return <AlertTriangle size={16} style={{ color: '#fbbf24' }} />
    return <XCircle size={16} style={{ color: '#f87171' }} />
  }

  const results = data?.results ?? {}
  const entries = Object.entries(results)
  const kinds = entries.map(([, val]) => {
    const v = val as Record<string, unknown>
    return statusKind(v?.status ?? v?.ok ?? val)
  })
  const healthy = kinds.filter(k => k === 'ok').length
  const degraded = kinds.filter(k => k === 'warn').length
  const failing = kinds.filter(k => k === 'bad').length

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="System Health"
        subtitle="Circuit breaker status and health checks"
        actions={
          <button className="btn btn-secondary btn-sm" onClick={refetch} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        }
      />

      {entries.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 24 }}>
          <GlassStatCard value={entries.length} label="Checks" icon={<Activity size={16} />} />
          <GlassStatCard value={healthy} label="Healthy" icon={<CheckCircle size={16} />} glow={healthy > 0 && failing === 0} />
          <GlassStatCard value={degraded} label="Degraded" icon={<AlertTriangle size={16} />} />
          <GlassStatCard value={failing} label="Failing" icon={<XCircle size={16} />} />
        </div>
      )}

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {(error || data?.error) && <Alert type="error">{error || data?.error}</Alert>}

      {data && !loading && (
        entries.length === 0
          ? <Empty message="No health check results." icon={<Activity size={32} />} />
          : (
            <GlassSection title="Health checks" className="animate-fade-in-up">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
              {entries.map(([key, val], i) => {
                const v = val as Record<string, unknown>
                const status = v?.status ?? v?.ok ?? val
                const fields = (typeof v === 'object' && v !== null)
                  ? Object.entries(v).filter(([k]) => k !== 'status' && k !== 'ok')
                  : []
                return (
                  <Card key={key} style={{ padding: '16px 18px', animationDelay: `${i * 40}ms` }} className="animate-fade-in-up">
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 10 }}>
                      <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>{key}</span>
                      {statusIcon(status)}
                    </div>
                    {typeof v === 'object' && v !== null ? (
                      fields.length > 0 ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                          {fields.map(([fk, fv]) => (
                            <div key={fk} style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 }}>
                              <span style={{ fontSize: 11.5, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600 }}>{fk}</span>
                              <span style={{ fontSize: 12.5, color: 'var(--text-secondary)', fontFamily: 'monospace', textAlign: 'right', wordBreak: 'break-word', minWidth: 0 }}>
                                {typeof fv === 'object' && fv !== null ? JSON.stringify(fv) : String(fv)}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>{String(status)}</p>
                      )
                    ) : (
                      <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{String(val)}</p>
                    )}
                  </Card>
                )
              })}
            </div>
            </GlassSection>
          )
      )}
    </div>
  )
}
