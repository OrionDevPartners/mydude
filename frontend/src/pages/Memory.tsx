import { useState } from 'react'
import { getMemory } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import { Search, Brain } from 'lucide-react'

export function Memory() {
  const [q, setQ] = useState('')
  const [layer, setLayer] = useState('')
  const { data, loading, error } = useApi(() => getMemory({ q: q || undefined, layer: layer || undefined }), [q, layer])

  return (
    <div>
      <PageHeader title="Memory Explorer" subtitle={`${data?.total ?? 0} memory layers stored`} />

      <div style={{ display: 'flex', gap: 10, marginBottom: 18 }}>
        <div style={{ position: 'relative', flex: 1 }}>
          <Search size={14} style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input className="form-input" style={{ paddingLeft: 34 }} placeholder="Search memory…" value={q} onChange={e => setQ(e.target.value)} />
        </div>
        {data && data.layer_types.length > 0 && (
          <select className="form-input" style={{ width: 180 }} value={layer} onChange={e => setLayer(e.target.value)}>
            <option value="">All layers</option>
            {data.layer_types.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        )}
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && (
        data.layers.length === 0
          ? <Empty message="No memory layers yet." icon={<Brain size={32} />} />
          : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {data.layers.map(l => (
                <Card key={l.id} style={{ padding: '14px 18px' }}>
                  <div style={{ display: 'flex', align: 'center', gap: 10, marginBottom: 8 }}>
                    <span className="badge badge-purple">{l.layer_type}</span>
                    {l.topic && <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{l.topic}</span>}
                    <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>{fmtDate(l.created_at)}</span>
                  </div>
                  {l.summary && <p style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-primary)', marginBottom: l.content ? 8 : 0 }}>{l.summary}</p>}
                  {l.content && (
                    <details>
                      <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--text-muted)', userSelect: 'none' }}>Full content</summary>
                      <pre style={{ marginTop: 10, fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', background: 'rgba(0,0,0,0.2)', padding: 12, borderRadius: 8 }}>
                        {l.content}
                      </pre>
                    </details>
                  )}
                </Card>
              ))}
            </div>
          )
      )}
    </div>
  )
}
