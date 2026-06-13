import { getSystem } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
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
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
              {entries.map(([key, val], i) => {
                const v = val as Record<string, unknown>
                const status = v?.status ?? v?.ok ?? val
                return (
                  <Card key={key} style={{ padding: '16px 18px', animationDelay: `${i * 40}ms` }} className="animate-fade-in-up">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                      {statusIcon(status)}
                      <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>{key}</span>
                    </div>
                    {typeof v === 'object' && v !== null ? (
                      <pre style={{ fontSize: 11.5, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                        {JSON.stringify(v, null, 2)}
                      </pre>
                    ) : (
                      <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{String(val)}</p>
                    )}
                  </Card>
                )
              })}
            </div>
          )
      )}
    </div>
  )
}
