import { useState } from 'react'
import { getProvenance } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import { Search, ChevronLeft, ChevronRight, GitBranch } from 'lucide-react'

export function Provenance() {
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const { data, loading, error } = useApi(() => getProvenance({ q: q || undefined, page }), [q, page])

  return (
    <div>
      <PageHeader title="Claim Provenance" subtitle="Track the origin and confidence of AI-generated claims" />

      <div style={{ position: 'relative', marginBottom: 18 }}>
        <Search size={14} style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
        <input className="form-input" style={{ paddingLeft: 34 }} placeholder="Search claims…" value={q} onChange={e => { setQ(e.target.value); setPage(1) }} />
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && (
        <>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>{data.total} records</p>
          {data.records.length === 0
            ? <Empty message="No provenance records yet." icon={<GitBranch size={32} />} />
            : (
              <div className="glass-card" style={{ overflow: 'hidden' }}>
                <table className="data-table">
                  <thead><tr><th>Claim</th><th>Role</th><th>Provider</th><th>Confidence</th><th>Verified</th><th>Time</th></tr></thead>
                  <tbody>
                    {data.records.map(r => (
                      <tr key={r.id}>
                        <td style={{ maxWidth: 260, fontSize: 12 }}>
                          <span title={r.claim_text}>{r.claim_text?.slice(0, 80)}{(r.claim_text?.length || 0) > 80 ? '…' : ''}</span>
                        </td>
                        <td style={{ fontSize: 11, fontFamily: 'monospace' }}>{r.origin_role}</td>
                        <td style={{ fontSize: 12 }}>{r.origin_provider}</td>
                        <td>
                          <span style={{ fontSize: 13, fontWeight: 600, color: confColor(r.confidence) }}>
                            {r.confidence != null ? (r.confidence * 100).toFixed(0) + '%' : '—'}
                          </span>
                        </td>
                        <td>{r.verified ? <span className="badge badge-green">yes</span> : <span className="badge badge-gray">no</span>}</td>
                        <td style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{fmtDate(r.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
          {data.total_pages > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12, marginTop: 18 }}>
              <button className="btn btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}><ChevronLeft size={14} /> Prev</button>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Page {data.page} of {data.total_pages}</span>
              <button className="btn btn-secondary btn-sm" disabled={page >= data.total_pages} onClick={() => setPage(p => p + 1)}>Next <ChevronRight size={14} /></button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function confColor(c: number): string {
  if (c >= 0.8) return '#34d399'
  if (c >= 0.5) return '#fbbf24'
  return '#f87171'
}
