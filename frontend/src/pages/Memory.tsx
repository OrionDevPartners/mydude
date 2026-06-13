import { useState } from 'react'
import { getMemory } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
import { fmtDate } from '@/lib/utils'
import { Search, Brain, Database, Layers, Cloud, ChevronLeft, ChevronRight } from 'lucide-react'

function num(v: unknown): number | null {
  return typeof v === 'number' ? v : null
}

function confColor(c: number): string {
  if (c >= 0.8) return '#34d399'
  if (c >= 0.5) return '#fbbf24'
  return '#f87171'
}

export function Memory() {
  const [q, setQ] = useState('')
  const [layer, setLayer] = useState('')
  const [category, setCategory] = useState('')
  const [adapter, setAdapter] = useState('')
  const [after, setAfter] = useState('')
  const [before, setBefore] = useState('')
  const [page, setPage] = useState(1)

  const { data, loading, error } = useApi(
    () => getMemory({
      q: q || undefined, layer: layer || undefined,
      category: category || undefined, adapter: adapter || undefined,
      after: after || undefined, before: before || undefined, page,
    }),
    [q, layer, category, adapter, after, before, page],
  )

  const sub = data?.substrate
  const localEntries = num(sub?.local?.cache_entries)
  const cloudEntries = num(sub?.cloud?.cache_entries)
  const events = data?.substrate_events ?? []
  const entries = data?.entries ?? []

  return (
    <div className="animate-fade-in">
      <PageHeader title="Memory Explorer" subtitle={`${data?.entry_total ?? 0} durable memories · ${data?.total ?? 0} memory layers`} />

      {data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 22 }}>
          <GlassStatCard value={data.entry_total} label="Durable memories" icon={<Brain size={16} />} glow={data.entry_total > 0} />
          <GlassStatCard value={data.total} label="Memory layers" icon={<Layers size={16} />} />
          <GlassStatCard value={localEntries ?? '—'} label="Local KG entries" icon={<Database size={16} />} />
          <GlassStatCard value={cloudEntries ?? '—'} label="Cloud entries" icon={<Cloud size={16} />} />
        </div>
      )}

      {sub && (localEntries !== null || cloudEntries !== null) && (
        <Card style={{ padding: '14px 18px', marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <Database size={14} style={{ color: 'var(--text-muted)' }} />
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>Durable Long-Term Memory</span>
            <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>persisted in the database — survives restarts</span>
          </div>
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            <div><div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>{localEntries ?? '—'}</div><div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Local KG entries</div></div>
            <div><div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>{cloudEntries ?? '—'}</div><div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Cloud entries</div></div>
          </div>
          {events.length > 0 && (
            <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5 }}>Recent activity</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {events.slice(0, 8).map((ev, i) => (
                  <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                    <span className="badge badge-purple" style={{ fontSize: 10 }}>{ev.type}</span>
                    <span style={{ fontSize: 12.5, color: 'var(--text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.detail}</span>
                    <span style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>{ev.timestamp ? fmtDate(new Date(ev.timestamp * 1000).toISOString()) : ''}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
      )}

      <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 220 }}>
          <Search size={14} style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input className="form-input" style={{ paddingLeft: 34 }} placeholder="Search memory…" value={q} onChange={e => { setQ(e.target.value); setPage(1) }} />
        </div>
        {data && data.entry_categories.length > 0 && (
          <select className="form-input" style={{ width: 170 }} value={category} onChange={e => { setCategory(e.target.value); setPage(1) }}>
            <option value="">All categories</option>
            {data.entry_categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
        {data && data.entry_adapters.length > 0 && (
          <select className="form-input" style={{ width: 150 }} value={adapter} onChange={e => { setAdapter(e.target.value); setPage(1) }}>
            <option value="">All adapters</option>
            {data.entry_adapters.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        )}
        {data && data.layer_types.length > 0 && (
          <select className="form-input" style={{ width: 150 }} value={layer} onChange={e => { setLayer(e.target.value); setPage(1) }}>
            <option value="">All layers</option>
            {data.layer_types.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, marginBottom: 18, flexWrap: 'wrap', alignItems: 'center' }}>
        <label style={{ fontSize: 11.5, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
          From
          <input type="date" className="form-input" style={{ width: 160 }} value={after} onChange={e => { setAfter(e.target.value); setPage(1) }} />
        </label>
        <label style={{ fontSize: 11.5, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
          To
          <input type="date" className="form-input" style={{ width: 160 }} value={before} onChange={e => { setBefore(e.target.value); setPage(1) }} />
        </label>
        {(after || before || category || adapter) && (
          <button className="btn btn-secondary btn-sm" onClick={() => { setAfter(''); setBefore(''); setCategory(''); setAdapter(''); setPage(1) }}>
            Clear filters
          </button>
        )}
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && (
        <>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>Long-term memories</div>
          {entries.length === 0
            ? <Empty message="No memories match these filters." icon={<Brain size={32} />} />
            : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {entries.map(e => (
                  <Card key={`${e.adapter}:${e.memory_id}`} style={{ padding: '14px 18px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
                      {e.category && <span className="badge badge-purple">{e.category}</span>}
                      {e.adapter && <span className="badge badge-blue">{e.adapter}</span>}
                      {e.verified && <span className="badge badge-green">verified</span>}
                      <span style={{ fontSize: 12, fontWeight: 600, color: confColor(e.confidence) }}>{(e.confidence * 100).toFixed(0)}%</span>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>{fmtDate(e.created_at)}</span>
                    </div>
                    <p style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-primary)', margin: 0, whiteSpace: 'pre-wrap' }}>{e.content}</p>
                    {e.source && <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>source: {e.source}</div>}
                  </Card>
                ))}
              </div>
            )
          }
          {data.entry_total_pages > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12, marginTop: 18 }}>
              <button className="btn btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
                <ChevronLeft size={14} /> Prev
              </button>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Page {data.entry_page} of {data.entry_total_pages}</span>
              <button className="btn btn-secondary btn-sm" disabled={page >= data.entry_total_pages} onClick={() => setPage(p => p + 1)}>
                Next <ChevronRight size={14} />
              </button>
            </div>
          )}

          {data.layers.length > 0 && (
            <div style={{ marginTop: 28 }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>Memory layers</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {data.layers.map(l => (
                  <Card key={l.id} style={{ padding: '14px 18px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
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
            </div>
          )}
        </>
      )}
    </div>
  )
}
