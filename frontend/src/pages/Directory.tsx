import { Link } from 'react-router-dom'
import { getDirectory } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
import { ExternalLink, CheckCircle, Globe, Plus, Grid3x3 } from 'lucide-react'

export function Directory() {
  const { data, loading, error } = useApi(getDirectory, [])

  const totalServices = data?.grouped.reduce((sum, g) => sum + g.services.length, 0) ?? 0
  const savedServices = data?.grouped.reduce((sum, g) => sum + g.services.filter(s => s.saved).length, 0) ?? 0

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Service Directory"
        subtitle="Supported integrations and services"
        actions={
          <Link to="/keys" className="btn btn-primary btn-sm">
            <Plus size={14} /> Add to vault
          </Link>
        }
      />

      {data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 24 }}>
          <GlassStatCard value={totalServices} label="Total services" icon={<Grid3x3 size={16} />} />
          <GlassStatCard value={savedServices} label="In vault" icon={<CheckCircle size={16} />} glow={savedServices > 0} />
          <GlassStatCard value={data.grouped.length} label="Categories" icon={<Globe size={16} />} />
        </div>
      )}

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && data.grouped.map(group => (
        <div key={group.category} style={{ marginBottom: 28 }}>
          <h2 style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 12 }}>
            {group.category}
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 12 }}>
            {group.services.map(svc => (
              <Card key={svc.slug} style={{ padding: '16px 18px', position: 'relative' }}>
                {svc.saved && (
                  <div style={{
                    position: 'absolute', top: 12, right: 12,
                    width: 8, height: 8, borderRadius: '50%', background: '#34d399',
                    boxShadow: '0 0 6px rgba(52,211,153,0.5)',
                  }} />
                )}
                <div style={{ marginBottom: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4 }}>
                    <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>{svc.name}</span>
                    {svc.saved && <CheckCircle size={13} style={{ color: '#34d399', flexShrink: 0 }} />}
                  </div>
                  {svc.env_var && (
                    <span style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-muted)' }}>{svc.env_var}</span>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 7 }}>
                  {svc.key_url && (
                    <a href={svc.key_url} target="_blank" rel="noopener noreferrer" className="btn btn-ghost btn-sm" style={{ fontSize: 11 }}>
                      <ExternalLink size={11} /> Get key
                    </a>
                  )}
                  {!svc.saved && (
                    <Link to={`/keys?provider=${svc.slug}`} className="btn btn-secondary btn-sm" style={{ fontSize: 11 }}>
                      <Plus size={11} /> Add
                    </Link>
                  )}
                </div>
              </Card>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
