import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getGovernance, ackAlert, setCloudShift, getEpistemicTrend, resetSwarmMetrics } from '@/lib/api'
import type { GovernanceProposal } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Spinner, Alert, Tabs, PageHeader, Empty } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
import { fmtDate } from '@/lib/utils'
import { ShieldCheck, Bell, BarChart2, Server, FlaskConical, HeartPulse, Activity, Vote } from 'lucide-react'

const EP_COLORS: Record<string, string> = {
  verified: '#34d399',
  derived: '#60a5fa',
  hypothesis: '#fbbf24',
  unknown: '#f87171',
}
const EP_LABELS = ['verified', 'derived', 'hypothesis', 'unknown'] as const

export function Governance() {
  const [searchParams, setSearchParams] = useSearchParams()
  const epWindow = searchParams.get('window') || '30'
  const [tab, setTab] = useState(searchParams.get('window') ? 'Epistemic' : 'Alerts')
  const { data, loading, error, refetch } = useApi(getGovernance, [])
  const { data: trend, loading: trendLoading, error: trendError } =
    useApi(() => getEpistemicTrend(epWindow), [epWindow])
  const [shiftBusy, setShiftBusy] = useState(false)
  const [shiftMsg, setShiftMsg] = useState<string | null>(null)
  const [shiftErr, setShiftErr] = useState<string | null>(null)
  const [resetBusy, setResetBusy] = useState<string | null>(null)
  const [resetMsg, setResetMsg] = useState<string | null>(null)
  const [resetErr, setResetErr] = useState<string | null>(null)

  function setWindow(w: string) {
    const next = new URLSearchParams(searchParams)
    next.set('window', w)
    setSearchParams(next, { replace: true })
  }

  async function handleAck(id: number) {
    await ackAlert(id)
    refetch()
  }

  async function handleResetMetrics(metric: string) {
    const label = metric === 'all' ? 'all swarm-health counters' : 'this counter'
    if (!window.confirm(`Reset ${label} to zero? This acknowledges the failures have been investigated.`)) return
    setResetBusy(metric)
    setResetMsg(null)
    setResetErr(null)
    try {
      await resetSwarmMetrics(metric)
      setResetMsg(metric === 'all' ? 'Swarm-health counters reset.' : 'Counter reset.')
      await refetch()
    } catch (e) {
      setResetErr(e instanceof Error ? e.message : 'Could not reset the counter.')
    } finally {
      setResetBusy(null)
    }
  }

  async function handleCloudShift(enable: boolean) {
    if (!enable && !window.confirm(
      'Disable cloud egress? All cloud providers will be dropped and task runs will fall through to local-only (degraded) or refuse.'
    )) return
    setShiftBusy(true)
    setShiftMsg(null)
    setShiftErr(null)
    try {
      const res = await setCloudShift(enable)
      setShiftMsg(res.warning || (res.cloud_shift_active
        ? 'Cloud egress enabled.'
        : 'Cloud egress disabled — running local-only.'))
      await refetch()
    } catch (e) {
      setShiftErr(e instanceof Error ? e.message : 'Could not update the kill switch.')
    } finally {
      setShiftBusy(false)
    }
  }

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><Spinner /></div>
  if (error) return <Alert type="error">{error}</Alert>

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Governance"
        subtitle="Sentinel alerts, performance ledger and provider metrics"
        actions={data?.open_alerts ? (
          <span className="badge badge-red">{data.open_alerts} open</span>
        ) : undefined}
      />

      {data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 22 }}>
          <GlassStatCard value={data.open_alerts ?? 0} label="Open alerts" icon={<Bell size={16} />} glow={(data.open_alerts ?? 0) === 0} />
          <GlassStatCard value={data.total_metrics ?? 0} label="Metric events" icon={<BarChart2 size={16} />} />
          <GlassStatCard value={data.metrics?.length ?? 0} label="Providers" icon={<Server size={16} />} />
          <GlassStatCard value={data.cloud_shift_active ? 'Cloud' : 'Local'} label="Routing mode" icon={<Activity size={16} />} glow={data.cloud_shift_active} />
          <GlassStatCard value={data.open_proposals ?? 0} label="Open proposals" icon={<Vote size={16} />} />
        </div>
      )}

      <Tabs tabs={['Alerts', 'Proposals', 'Swarm Health', 'Ledger', 'Metrics', 'Epistemic', 'Jurisdiction']} active={tab} onChange={setTab} />

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

      {tab === 'Proposals' && data && (
        (data.proposals?.length ?? 0) === 0 && (data.recent_proposals?.length ?? 0) === 0
          ? <Empty message="No governance proposals yet. The auditor and sentinel raise proposals automatically when they detect a tuning, policy, or safety concern." icon={<Vote size={32} />} />
          : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                A proposal must clear a minimum participation floor before quorum can auto-enact or auto-reject it —
                a single unanimous vote can't decide on its own. This view is read-only.
              </p>
              {(data.proposals ?? []).map(p => <ProposalCard key={p.id} p={p} />)}
              {(data.recent_proposals?.length ?? 0) > 0 && (
                <>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', marginTop: 8 }}>Recently resolved</div>
                  {(data.recent_proposals ?? []).map(p => <ProposalCard key={p.id} p={p} />)}
                </>
              )}
            </div>
          )
      )}

      {tab === 'Swarm Health' && data && (
        <div>
          <div className="glass-card" style={{ padding: 18, marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <HeartPulse size={18} color="var(--text-secondary)" />
              <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>Silent-failure counters</span>
              <button
                className="btn btn-secondary btn-sm"
                style={{ marginLeft: 'auto' }}
                disabled={resetBusy !== null || (!data.failed_indexes && !data.governance_proposal_failures)}
                onClick={() => handleResetMetrics('all')}
              >
                {resetBusy === 'all' ? 'Working…' : 'Reset all'}
              </button>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
              Critical swarm paths that fail quietly are counted here so they don't disappear into the logs.
              Once you've investigated a spike, reset the counter to restore the signal of a fresh failure.
            </p>
            {data.metrics_reset_at && (
              <p style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
                Last reset {fmtDate(data.metrics_reset_at)}{data.metrics_reset_by ? ` by ${data.metrics_reset_by}` : ''}.
              </p>
            )}
            {resetMsg && <div style={{ marginTop: 12 }}><Alert type="info">{resetMsg}</Alert></div>}
            {resetErr && <div style={{ marginTop: 12 }}><Alert type="error">{resetErr}</Alert></div>}
          </div>
          <div className="glass-card" style={{ overflow: 'hidden' }}>
            <table className="data-table">
              <thead><tr><th>Metric</th><th>Count</th><th>Meaning</th><th></th></tr></thead>
              <tbody>
                <HealthRow
                  label="Failed indexes"
                  count={data.failed_indexes}
                  meaning="Run-index writes that failed after a completed run (history/search may be incomplete)."
                  busy={resetBusy === 'metric_failed_indexes'}
                  disabled={resetBusy !== null}
                  onReset={() => handleResetMetrics('metric_failed_indexes')}
                />
                <HealthRow
                  label="Governance proposal raise failures"
                  count={data.governance_proposal_failures}
                  meaning="Auditor meta-claims that could not be converted into governance proposals."
                  busy={resetBusy === 'metric_governance_proposal_failures'}
                  disabled={resetBusy !== null}
                  onReset={() => handleResetMetrics('metric_governance_proposal_failures')}
                />
              </tbody>
            </table>
          </div>
        </div>
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

      {tab === 'Epistemic' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
            <span style={{ fontSize: 12, color: 'var(--text-muted)', marginRight: 4 }}>Window:</span>
            {(trend?.windows ?? [
              { key: '10', label: 'Last 10 runs' },
              { key: '30', label: 'Last 30 runs' },
              { key: '100', label: 'Last 100 runs' },
              { key: '24h', label: 'Last 24 hours' },
              { key: '7d', label: 'Last 7 days' },
              { key: '30d', label: 'Last 30 days' },
            ]).map(w => (
              <button
                key={w.key}
                className={`btn btn-sm ${epWindow === w.key ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setWindow(w.key)}
              >
                {w.label}
              </button>
            ))}
          </div>

          {trendLoading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
          {trendError && <Alert type="error">{trendError}</Alert>}

          {trend && (
            <>
              <div className="glass-card" style={{ padding: 18, marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 14 }}>
                  <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>Epistemic Label Trend</span>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    {trend.window_label} · {trend.run_count} run{trend.run_count === 1 ? '' : 's'}
                  </span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 12 }}>
                  <Stat value={`${trend.verified_ratio}%`} label="Verified (window)" color={EP_COLORS.verified} />
                  <Stat value={`${trend.unknown_ratio}%`} label="Unknown (window)" color={EP_COLORS.unknown} />
                  <Stat value={String(trend.grand_total)} label="Claims in window" />
                  <Stat
                    value={trend.grand_total === 0 ? '—' : (trend.unknown_ratio > trend.verified_ratio ? '⚠ Unverified drift' : '✓ Verified-led')}
                    label="Verified vs. unknown"
                    color={trend.grand_total === 0 ? undefined : (trend.unknown_ratio > trend.verified_ratio ? EP_COLORS.unknown : EP_COLORS.verified)}
                  />
                </div>
              </div>

              <div className="glass-card" style={{ padding: 18 }}>
                <div style={{ display: 'flex', gap: 16, marginBottom: 14, flexWrap: 'wrap' }}>
                  {EP_LABELS.map(label => (
                    <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
                      <span style={{ width: 11, height: 11, borderRadius: 2, background: EP_COLORS[label] }} />
                      {label}
                    </span>
                  ))}
                </div>
                {trend.points.length === 0 ? (
                  <Empty
                    message="No indexed runs in this window. Try a wider window or run more tasks."
                    icon={<FlaskConical size={32} />}
                  />
                ) : (
                  <>
                    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 160, overflowX: 'auto', paddingBottom: 4 }}>
                      {trend.points.map(pt => (
                        <div
                          key={pt.run_id}
                          title={`${pt.created_at ? fmtDate(pt.created_at) : ''} — ${EP_LABELS.map(l => `${l}: ${pt.counts[l] ?? 0}`).join('  ')}`}
                          style={{
                            display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
                            minWidth: 8, flex: '1 0 8px', height: '100%',
                            background: pt.total === 0 ? 'var(--surface-2, rgba(255,255,255,0.04))' : 'transparent',
                            borderRadius: 2,
                          }}
                        >
                          {EP_LABELS.map(label => (
                            pt.pct[label] > 0 ? (
                              <div key={label} style={{ height: `${pt.pct[label]}%`, background: EP_COLORS[label] }} />
                            ) : null
                          ))}
                        </div>
                      ))}
                    </div>
                    <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 10 }}>
                      Each bar is one run (oldest → newest), showing the share of each epistemic label in that run's claim ledger.
                    </p>
                  </>
                )}
              </div>
            </>
          )}
        </div>
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
              {data.cloud_shift_active ? (
                <button className="btn btn-danger btn-sm" disabled={shiftBusy} onClick={() => handleCloudShift(false)}>
                  {shiftBusy ? 'Working…' : 'Disable cloud egress'}
                </button>
              ) : (
                <button className="btn btn-primary btn-sm" disabled={shiftBusy} onClick={() => handleCloudShift(true)}>
                  {shiftBusy ? 'Working…' : 'Re-enable cloud egress'}
                </button>
              )}
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 10 }}>
              Disabling drops every cloud provider during an incident — task runs fall through to local-only
              (degraded) or refuse, with no redeploy required.
            </p>
            {shiftMsg && <div style={{ marginTop: 12 }}><Alert type="info">{shiftMsg}</Alert></div>}
            {shiftErr && <div style={{ marginTop: 12 }}><Alert type="error">{shiftErr}</Alert></div>}
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

function HealthRow({ label, count, meaning, busy, disabled, onReset }: {
  label: string; count: number; meaning: string; busy: boolean; disabled: boolean; onReset: () => void
}) {
  return (
    <tr>
      <td style={{ fontWeight: 600 }}>{label}</td>
      <td>
        <span className={`badge badge-${count > 0 ? 'yellow' : 'green'}`}>{count}</span>
      </td>
      <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{meaning}</td>
      <td style={{ textAlign: 'right' }}>
        {count > 0 && (
          <button className="btn btn-secondary btn-sm" disabled={disabled} onClick={onReset}>
            {busy ? 'Working…' : 'Reset'}
          </button>
        )}
      </td>
    </tr>
  )
}

function scoreColor(score: number): string {
  if (score >= 0.8) return '#34d399'
  if (score >= 0.5) return '#fbbf24'
  return '#f87171'
}

function trackBadge(track: string): string {
  if (track === 'safety') return 'badge-red'
  if (track === 'policy') return 'badge-yellow'
  return 'badge-blue'
}

function statusBadge(status: string): string {
  if (status === 'enacted') return 'badge-green'
  if (status === 'rejected') return 'badge-red'
  if (status === 'open') return 'badge-blue'
  return 'badge-gray'
}

function ProposalCard({ p }: { p: GovernanceProposal }) {
  const yesPct = Math.round((p.yes_ratio ?? 0) * 100)
  const quorumPct = Math.round((p.quorum_threshold ?? 0) * 100)
  const quorumMet = p.total_effective > 0 && (p.yes_ratio ?? 0) >= (p.quorum_threshold ?? 0)
  const part = p.participation
  // When a weight floor is active, the meter tracks the binding constraint (the
  // dimension furthest from being met) so it never reads "full" while one of the
  // two floor dimensions is still short.
  const partProgress = part
    ? (part.min_weight > 0 ? Math.min(part.voters_progress, part.weight_progress) : part.voters_progress)
    : 0
  const partPct = Math.round(partProgress * 100)
  const hasVotes = p.total_effective > 0 || p.vote_count > 0
  return (
    <div className="glass-card" style={{ padding: '14px 18px', opacity: p.status === 'open' ? 1 : 0.7 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11.5, fontFamily: 'monospace', color: 'var(--text-muted)' }}>{p.proposal_id}</span>
        <span className={`badge ${trackBadge(p.track)}`}>{p.track}</span>
        <span className={`badge ${statusBadge(p.status)}`}>{p.status}</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>{fmtDate(p.created_at)}</span>
      </div>
      <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)', marginBottom: p.proposed_action ? 2 : 10 }}>{p.title}</div>
      {p.proposed_action && (
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 10 }}>→ {p.proposed_action}</div>
      )}
      {hasVotes ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
          <Meter
            label={`${yesPct}% yes`}
            sub={`quorum ${quorumPct}% · ${p.vote_count} vote${p.vote_count === 1 ? '' : 's'}`}
            fillPct={yesPct}
            markerPct={quorumPct}
            met={quorumMet}
          />
          <Meter
            label={`${part?.participation_voters ?? 0}/${part?.min_voters ?? 0} voters`}
            sub={
              (part && part.min_weight > 0 ? `wt ${part.participation_weight}/${part.min_weight} · ` : '') +
              (part?.participation_met ? 'floor met' : 'held open below floor')
            }
            fillPct={partPct}
            met={!!part?.participation_met}
          />
        </div>
      ) : (
        <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>No votes yet.</p>
      )}
    </div>
  )
}

function Meter({ label, sub, fillPct, markerPct, met }: {
  label: string; sub: string; fillPct: number; markerPct?: number; met: boolean
}) {
  const clamped = Math.max(0, Math.min(100, fillPct))
  return (
    <div>
      <div style={{ position: 'relative', height: 8, background: 'rgba(255,255,255,0.08)', border: '1px solid var(--border, rgba(255,255,255,0.1))', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: 0, left: 0, bottom: 0, width: `${clamped}%`, background: met ? '#34d399' : '#60a5fa' }} />
        {typeof markerPct === 'number' && (
          <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${Math.max(0, Math.min(100, markerPct))}%`, width: 2, background: 'var(--text-primary)' }} title={`Quorum ${markerPct}%`} />
        )}
      </div>
      <div style={{ fontSize: 11.5, marginTop: 4 }}>
        <span style={{ fontWeight: 600, color: met ? '#34d399' : 'var(--text-secondary)' }}>{label}</span>
        <span style={{ color: 'var(--text-muted)' }}> · {sub}</span>
      </div>
    </div>
  )
}

function Stat({ value, label, color }: { value: string; label: string; color?: string }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 20, fontWeight: 700, color: color || 'var(--text-primary)' }}>{value}</div>
      <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 2 }}>{label}</div>
    </div>
  )
}
