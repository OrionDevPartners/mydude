import { useState } from 'react'
import { getGovernance, ackAlert } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, Tabs, PageHeader, Empty, Badge } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import { ShieldCheck, Bell, BarChart2, Server } from 'lucide-react'

export function Governance() {
  const [tab, setTab] = useState('Alerts')
  const { data, loading, error, refetch } = useApi(getGovernance, [])

  async function handleAck(id: number) {
    await ackAlert(id)
    refetch()
  }

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><Spinner /></div>
  if (error) return <Alert type="error">{error}</Alert>

  return (
    <div>
      <PageHeader
        title="Governance"
        subtitle="Sentinel alerts, performance ledger and provider metrics"
        actions={data?.open_alerts ? (
          <span className="badge badge-red">{data.open_alerts} open</span>
        ) : undefined}
      />

      <Tabs tabs={['Alerts', 'Ledger', 'Metrics', 'Jurisdiction']} active={tab} onChange={setTab} />

      {tab === 'Alerts' && data && (
        data.alerts.length === 0
          ? <Empty message="No sentinel alerts." icon={<Bell size={32} />} />
          : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {data.alerts.map(a => (
                <div key={a.id} className="glass-card" style={{
                  padding: '14px 18px', display: 'flex', alignItems: 'flex-start', gap: 14,
                  opacity: a.acknowledged ? 0.55 : 1,
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', align: 'center', gap: 8, marginBottom: 4 }}>
                      <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>{a.rule}</span>
                      <span className={`badge badge-${a.severity === 'high' ? 'red' : a.severity === 'medium' ? 'yellow' : 'blue'}`}>
                        {a.severity}
                      </span>
                      {a.acknowledged && <span className="badge badge-gray">ack</span>}
                    </div>
                    <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 4 }}>{a.detail}</p>
                    <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>{fmtDate(a.created_at)}</p>
                  </div>
                  {!a.acknowledged && (
                    <button className="btn btn-secondary btn-sm" onClick={() => handleAck(a.id)}>Ack</button>
                  )}
                </div>
              ))}
            </div>
          )
      )}

      {tab === 'Ledger' && data && (
        data.ledger.length === 0
          ? <Empty message="No ledger entries." icon={<ShieldCheck size={32} />} />
          : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead><tr><th>Role</th><th>Provider</th><th>Score</th><th>Detail</th><th>Time</th></tr></thead>
                <tbody>
                  {data.ledger.map(l => (
                    <tr key={l.id}>
                      <td style={{ fontSize: 12, fontFamily: 'monospace' }}>{l.agent_role}</td>
                      <td style={{ fontSize: 12 }}>{l.provider}</td>
                      <td><span style={{ fontSize: 13, fontWeight: 700, color: scoreColor(l.score) }}>{l.score?.toFixed ? l.score.toFixed(2) : l.score}</span></td>
                      <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{l.detail}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{fmtDate(l.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
      )}

      {tab === 'Metrics' && data && (
        data.metrics.length === 0
          ? <Empty message="No provider metrics yet." icon={<BarChart2 size={32} />} />
          : (
            <div>
              <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>{data.total_metrics} total metric events</p>
              <div className="glass-card" style={{ overflow: 'hidden' }}>
                <table className="data-table">
                  <thead><tr><th>Provider</th><th>Calls</th><th>Avg Latency</th><th>Success Rate</th><th>Avg Rating</th></tr></thead>
                  <tbody>
                    {data.metrics.map(m => (
                      <tr key={m.provider}>
                        <td style={{ fontWeight: 600 }}>{m.provider}</td>
                        <td>{m.calls}</td>
                        <td>{m.avg_latency}ms</td>
                        <td>
                          <span style={{ color: m.success_rate >= 90 ? '#34d399' : m.success_rate >= 70 ? '#fbbf24' : '#f87171', fontWeight: 600 }}>
                            {m.success_rate}%
                          </span>
                        </td>
                        <td>{m.avg_rating ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )
      )}

      {tab === 'Jurisdiction' && data && (
        <div>
          <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div>
                <p className="form-label" style={{ marginBottom: 2 }}>Cloud Shift Kill Switch</p>
                <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                  {data.cloud_shift_active ? 'Active — cloud routing enabled' : 'Inactive — local-only mode'}
                </p>
              </div>
              <span className={`badge ${data.cloud_shift_active ? 'badge-green' : 'badge-gray'}`} style={{ marginLeft: 'auto' }}>
                {data.cloud_shift_active ? 'enabled' : 'disabled'}
              </span>
            </div>
          </div>
          {data.exec_locus_dist.length === 0
            ? <Empty message="No exec locus data." icon={<Server size={32} />} />
            : <pre style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{JSON.stringify(data.exec_locus_dist, null, 2)}</pre>
          }
        </div>
      )}
    </div>
  )
}

function scoreColor(score: number): string {
  if (score >= 0.8) return '#34d399'
  if (score >= 0.5) return '#fbbf24'
  return '#f87171'
}
