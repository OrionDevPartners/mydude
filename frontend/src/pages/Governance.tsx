import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getGovernance, ackAlert, setCloudShift, getEpistemicTrend, resetSwarmMetrics, getGuardrailEvents, voteOnProposal } from '@/lib/api'
import type { GovernanceProposal, DriftEntry, PreconditionGapEntry, GuardrailEvent } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Spinner, Alert, Tabs, PageHeader, Empty } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
import { fmtDate } from '@/lib/utils'
import { ShieldCheck, Bell, BarChart2, Server, FlaskConical, HeartPulse, Activity, Vote, Zap, GitBranch, PackageSearch, ShieldAlert } from 'lucide-react'
import type { AcquisitionJob } from '@/lib/api'

const EP_COLORS: Record<string, string> = {
  verified: '#34d399',
  derived: '#60a5fa',
  hypothesis: '#fbbf24',
  unknown: '#f87171',
}
const EP_LABELS = ['verified', 'derived', 'hypothesis', 'unknown'] as const

export function Governance() {
  const [searchParams, setSearchParams] = useSearchParams()
  const epFrom = searchParams.get('from') || ''
  const epTo = searchParams.get('to') || ''
  const isCustomRange = Boolean(epFrom || epTo)
  const epWindow = isCustomRange ? 'custom' : (searchParams.get('window') || '30')
  const [tab, setTab] = useState(
    searchParams.get('window') || epFrom || epTo ? 'Epistemic' : 'Alerts'
  )
  const { data, loading, error, refetch } = useApi(getGovernance, [])
  const { data: trend, loading: trendLoading, error: trendError } =
    useApi(() => getEpistemicTrend({ window: epWindow, from: epFrom, to: epTo }), [epWindow, epFrom, epTo])
  const { data: guardrails, loading: grLoading, error: grError } =
    useApi(() => getGuardrailEvents({ limit: 100 }), [])
  const [shiftBusy, setShiftBusy] = useState(false)
  const [shiftMsg, setShiftMsg] = useState<string | null>(null)
  const [shiftErr, setShiftErr] = useState<string | null>(null)
  const [resetBusy, setResetBusy] = useState<string | null>(null)
  const [resetMsg, setResetMsg] = useState<string | null>(null)
  const [resetErr, setResetErr] = useState<string | null>(null)

  function setWindow(w: string) {
    const next = new URLSearchParams(searchParams)
    next.set('window', w)
    next.delete('from')
    next.delete('to')
    setSearchParams(next, { replace: true })
  }

  function setCustomRange(from: string, to: string) {
    const next = new URLSearchParams(searchParams)
    next.delete('window')
    if (from) next.set('from', from); else next.delete('from')
    if (to) next.set('to', to); else next.delete('to')
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

      <Tabs tabs={['Alerts', 'Proposals', 'Guardrails', 'Swarm Health', 'Ledger', 'Metrics', 'Epistemic', 'Jurisdiction', 'Routing', 'Acquisition']} active={tab} onChange={setTab} />

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

      {tab === 'Guardrails' && (
        grLoading
          ? <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>
          : grError
            ? <Alert type="error">{grError}</Alert>
            : (
              <div>
                <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>
                  Substrate guardrail classifiers wrap every swarm run. <strong>Ingress</strong> screens the
                  prompt for injection / jailbreak attempts and redacts PII &amp; secrets before inference;
                  <strong> egress</strong> runs output-safety and code-shield checks before results are surfaced.
                  Blocks &amp; flags also raise sentinel alerts and adjust compliance / hallucination scores.
                </p>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 18 }}>
                  <GlassStatCard value={guardrails?.total_blocks ?? 0} label="Blocks" icon={<ShieldAlert size={16} />} glow={(guardrails?.total_blocks ?? 0) > 0} />
                  <GlassStatCard value={guardrails?.total_flags ?? 0} label="Flags" icon={<Bell size={16} />} />
                  <GlassStatCard value={guardrails?.total_redacts ?? 0} label="Redactions" icon={<ShieldCheck size={16} />} />
                  <GlassStatCard value={guardrails?.total_events ?? 0} label="Total verdicts" icon={<BarChart2 size={16} />} />
                </div>
                {(guardrails?.events.length ?? 0) === 0
                  ? <Empty message="No guardrail blocks, flags or redactions recorded yet. The ingress (injection + PII) and egress (output safety + code shield) classifiers are active on every run." icon={<ShieldCheck size={32} />} />
                  : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {(guardrails?.events ?? []).map(ev => <GuardrailEventCard key={ev.id} ev={ev} />)}
                    </div>
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
                <thead><tr><th>Wave</th><th>Avg CS</th><th>Avg HR</th><th>Agents</th><th>Consensus</th><th>Dissent</th><th>Time</th></tr></thead>
                <tbody>
                  {data.ledger.map(l => (
                    <tr key={l.id}>
                      <td style={{ fontSize: 12, fontFamily: 'monospace' }}>{l.wave_idx}</td>
                      <td><span style={{ fontSize: 13, fontWeight: 700, color: scoreColor((l.avg_cs ?? 0) / 100) }}>{l.avg_cs?.toFixed ? l.avg_cs.toFixed(2) : l.avg_cs}</span></td>
                      <td><span style={{ fontSize: 13, fontWeight: 700, color: scoreColor(1 - (l.avg_hr ?? 0)) }}>{l.avg_hr?.toFixed ? l.avg_hr.toFixed(2) : l.avg_hr}</span></td>
                      <td style={{ fontSize: 12 }}>{l.agent_count}</td>
                      <td><span style={{ fontSize: 13, fontWeight: 700, color: scoreColor(l.consensus_confidence) }}>{l.consensus_confidence?.toFixed ? l.consensus_confidence.toFixed(2) : l.consensus_confidence}</span></td>
                      <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{l.dissent_count}</td>
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

          <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
            <span style={{ fontSize: 12, color: 'var(--text-muted)', marginRight: 4 }}>
              Custom range:
            </span>
            <input
              type="date"
              className="input"
              aria-label="From date"
              value={epFrom}
              max={epTo || undefined}
              onChange={e => setCustomRange(e.target.value, epTo)}
              style={{ width: 'auto', padding: '4px 8px', fontSize: 13 }}
            />
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>to</span>
            <input
              type="date"
              className="input"
              aria-label="To date"
              value={epTo}
              min={epFrom || undefined}
              onChange={e => setCustomRange(epFrom, e.target.value)}
              style={{ width: 'auto', padding: '4px 8px', fontSize: 13 }}
            />
            {isCustomRange && (
              <button
                className="btn btn-sm btn-secondary"
                onClick={() => setWindow('30')}
              >
                Clear
              </button>
            )}
            {isCustomRange && (
              <span className="badge badge-blue" style={{ fontSize: 11 }}>
                Custom range active
              </span>
            )}
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

      {tab === 'Routing' && data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Zero-Token Router Panel */}
          <div className="glass-card" style={{ padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
              <Zap size={18} color="var(--text-secondary)" />
              <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>Zero-Token Router</span>
              <span className="badge badge-blue" style={{ fontSize: 10, marginLeft: 4 }}>
                {data.routing_stats?.embedding_backend ?? 'tfidf-fallback'}
              </span>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>
              When an intent matches a known tool above the confidence threshold ({((data.routing_stats?.threshold ?? 0.92) * 100).toFixed(0)}%),
              it is dispatched directly — bypassing the LLM swarm entirely and saving all inference tokens.
              Below threshold, the swarm handles it normally.
            </p>
            {!data.routing_stats || data.routing_stats.total_evaluations === 0 ? (
              <Empty message="No routing evaluations recorded yet. Stats populate as tasks are run." icon={<Zap size={28} />} />
            ) : (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 12, marginBottom: 16 }}>
                  <Stat value={`${data.routing_stats.hit_rate_pct}%`} label="Hit rate" color={data.routing_stats.hit_rate_pct > 10 ? '#34d399' : 'var(--text-primary)'} />
                  <Stat value={String(data.routing_stats.zero_token_hits)} label="Zero-token hits" color={data.routing_stats.zero_token_hits > 0 ? '#34d399' : 'var(--text-muted)'} />
                  <Stat value={String(data.routing_stats.total_evaluations)} label="Total evaluated" />
                  <Stat value={`${((data.routing_stats.threshold ?? 0.92) * 100).toFixed(0)}%`} label="Threshold" />
                </div>
                {data.routing_stats.last_hit_capability && (
                  <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    Last hit: <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>{data.routing_stats.last_hit_capability}</span>
                    {' '}at <span style={{ color: 'var(--text-secondary)' }}>{(data.routing_stats.last_hit_score * 100).toFixed(1)}%</span> confidence
                  </div>
                )}
                {data.routing_stats.last_reset_at && (
                  <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
                    Last reset: {fmtDate(data.routing_stats.last_reset_at)}
                  </p>
                )}
              </>
            )}
          </div>

          {/* Capability Drift Panel */}
          <div className="glass-card" style={{ padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <GitBranch size={18} color="var(--text-secondary)" />
              <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>Capability Drift</span>
              {data.drift_report && (
                <span className={`badge ${(data.drift_report.total_drift ?? 0) === 0 ? 'badge-green' : 'badge-yellow'}`} style={{ marginLeft: 4 }}>
                  {(data.drift_report.total_drift ?? 0) === 0 ? 'no drift' : `${data.drift_report.total_drift} drift`}
                </span>
              )}
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>
              Diffs declared capability contracts (the spec) against broker.py's actual dispatch table (the reality).
              Orphaned declarations have a contract but no handler; undeclared handlers are implemented but ungoverned.
            </p>
            {!data.drift_report ? (
              <Empty message="Drift report unavailable." icon={<GitBranch size={28} />} />
            ) : data.drift_report.error ? (
              <Alert type="error">Drift scan error: {data.drift_report.error}</Alert>
            ) : (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 12, marginBottom: 16 }}>
                  <Stat value={String(data.drift_report.declared_count)} label="Declared" />
                  <Stat value={String(data.drift_report.handled_count)} label="Handled" />
                  <Stat
                    value={String(data.drift_report.orphaned_count)}
                    label="Orphaned declarations"
                    color={data.drift_report.orphaned_count > 0 ? '#fbbf24' : '#34d399'}
                  />
                  <Stat
                    value={String(data.drift_report.undeclared_count)}
                    label="Undeclared handlers"
                    color={data.drift_report.undeclared_count > 0 ? '#fbbf24' : '#34d399'}
                  />
                </div>
                <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
                  Scanned {fmtDate(data.drift_report.scanned_at)} · cached 5 min
                </p>
                {data.drift_report.orphaned.length > 0 && (
                  <div style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
                      Orphaned declarations ({data.drift_report.orphaned.length})
                    </div>
                    <div className="glass-card" style={{ overflow: 'hidden', padding: 0 }}>
                      <table className="data-table">
                        <thead><tr><th>Capability</th><th>Reason</th><th>Severity</th></tr></thead>
                        <tbody>
                          {data.drift_report.orphaned.map((e: DriftEntry) => (
                            <tr key={e.capability}>
                              <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{e.capability}</td>
                              <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{e.reason}</td>
                              <td><span className={`badge badge-${e.severity === 'warning' ? 'yellow' : 'gray'}`}>{e.severity}</span></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                {data.drift_report.undeclared.length > 0 && (
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
                      Undeclared handlers ({data.drift_report.undeclared.length})
                    </div>
                    <div className="glass-card" style={{ overflow: 'hidden', padding: 0 }}>
                      <table className="data-table">
                        <thead><tr><th>Capability</th><th>Reason</th><th>Severity</th></tr></thead>
                        <tbody>
                          {data.drift_report.undeclared.map((e: DriftEntry) => (
                            <tr key={e.capability}>
                              <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{e.capability}</td>
                              <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{e.reason}</td>
                              <td><span className={`badge badge-${e.severity === 'warning' ? 'yellow' : 'gray'}`}>{e.severity}</span></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                {(data.drift_report.precondition_gaps ?? []).length > 0 && (
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
                      Precondition gaps — call-graph drift ({(data.drift_report.precondition_gaps ?? []).length})
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
                      These handlers make outbound integration calls but their contract declares zero enforced preconditions — latent governance gaps.
                    </div>
                    <div className="glass-card" style={{ overflow: 'hidden', padding: 0 }}>
                      <table className="data-table">
                        <thead><tr><th>Capability</th><th>Integration Calls</th><th>Preconditions</th><th>Severity</th></tr></thead>
                        <tbody>
                          {(data.drift_report.precondition_gaps ?? []).map((g: PreconditionGapEntry) => (
                            <tr key={g.capability}>
                              <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{g.capability}</td>
                              <td style={{ fontSize: 12, color: 'var(--text-muted)', fontFamily: 'monospace' }}>{g.integration_calls.join(', ')}</td>
                              <td><span className="badge badge-red">{g.precondition_count}</span></td>
                              <td><span className={`badge badge-${g.severity === 'warning' ? 'yellow' : 'gray'}`}>{g.severity}</span></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                {data.drift_report.total_drift === 0 && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#34d399', fontSize: 13 }}>
                    <ShieldCheck size={16} />
                    All declared capabilities have broker handlers and all handlers have declared contracts.
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {tab === 'Acquisition' && data && (
        <AcquisitionPanel jobs={data.acquisition_jobs ?? []} enabled={data.acquisition_enabled ?? false} onVoted={refetch} />
      )}
    </div>
  )
}

function AcquisitionPanel({ jobs, enabled, onVoted }: { jobs: AcquisitionJob[]; enabled: boolean; onVoted: () => void }) {
  const [expanded, setExpanded] = useState<number | null>(null)
  const [voteBusy, setVoteBusy] = useState<number | null>(null)
  const [voteErr, setVoteErr] = useState<Record<number, string>>({})

  async function handleVote(job: AcquisitionJob, vote: 'yes' | 'no') {
    if (job.governance_proposal_db_id == null) return
    setVoteBusy(job.id)
    setVoteErr(prev => { const n = { ...prev }; delete n[job.id]; return n })
    try {
      await voteOnProposal(job.governance_proposal_db_id, vote)
      await onVoted()
    } catch (e) {
      setVoteErr(prev => ({ ...prev, [job.id]: e instanceof Error ? e.message : 'Vote failed.' }))
    } finally {
      setVoteBusy(null)
    }
  }

  const stateColor = (s: string) => {
    if (s === 'approved') return '#34d399'
    if (s === 'rejected' || s === 'failed') return '#f87171'
    if (s === 'governance_pending') return '#fbbf24'
    if (s === 'fetching' || s === 'sandboxing') return '#60a5fa'
    return 'var(--text-muted)'
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="glass-card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <PackageSearch size={16} style={{ opacity: 0.7, flexShrink: 0 }} />
        <div style={{ flex: 1, fontSize: 13, color: 'var(--text-secondary)' }}>
          When the broker encounters an unmet capability, the acquisition loop searches PyPI/npm, verifies
          candidates in a sandboxed subprocess (no production secrets), runs the governance envelope,
          and raises a <strong>safety-track governance proposal</strong> for your explicit approval.
          Integration only occurs after sandbox pass + governance pass + you enact the proposal.
        </div>
        <span className={`badge ${enabled ? 'badge-green' : 'badge-gray'}`} style={{ flexShrink: 0 }}>
          {enabled ? 'Enabled' : 'Disabled'}
        </span>
      </div>

      {!enabled && (
        <div className="glass-card" style={{ padding: '12px 16px', fontSize: 12.5, color: 'var(--text-muted)' }}>
          Set <code>ENABLE_AUTO_SIPHON_ACQUISITION=true</code> in your environment secrets to activate
          the auto-siphon acquisition loop. Disabled by default — it fetches and executes third-party code.
        </div>
      )}

      {jobs.length === 0 ? (
        <Empty
          message={enabled
            ? "No acquisition jobs yet. Jobs open automatically when the broker encounters an unmet capability with no in-codebase equivalent."
            : "No acquisition jobs. Enable the kill switch to start collecting jobs."
          }
          icon={<PackageSearch size={32} />}
        />
      ) : (
        jobs.map(job => (
          <div key={job.id} className="glass-card" style={{ padding: '14px 18px' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 8 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
                  <code style={{ fontSize: 11, color: 'var(--text-muted)' }}>{job.job_id}</code>
                  <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                    {job.capability}
                  </span>
                  <span style={{ fontWeight: 700, fontSize: 12, color: stateColor(job.state) }}>
                    {job.state}
                  </span>
                </div>
                {job.best_candidate_name && (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
                    Best candidate:{' '}
                    <code>{job.best_candidate_name}{job.best_candidate_version ? `==${job.best_candidate_version}` : ''}</code>
                    {job.best_candidate_registry && (
                      <span style={{ color: 'var(--text-muted)' }}> ({job.best_candidate_registry})</span>
                    )}
                  </div>
                )}
                {job.governance_proposal_id && (
                  <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    Governance proposal: <code>{job.governance_proposal_id}</code>
                    {' '}<span className="badge badge-yellow" style={{ fontSize: 10 }}>awaiting approval</span>
                  </div>
                )}
                {job.notes && (
                  <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 4, maxWidth: 600 }}>{job.notes}</div>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{fmtDate(job.created_at)}</div>
            </div>

            {job.candidates.length > 0 && (
              <>
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => setExpanded(expanded === job.id ? null : job.id)}
                  style={{ marginTop: 4, fontSize: 11 }}
                >
                  {expanded === job.id ? 'Hide' : `Show ${job.candidates.length} candidate${job.candidates.length === 1 ? '' : 's'}`}
                </button>
                {expanded === job.id && (
                  <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {job.candidates.map(c => (
                      <div key={c.id} style={{
                        padding: '8px 12px',
                        background: 'rgba(255,255,255,0.04)',
                        borderRadius: 6,
                        border: '1px solid var(--border, rgba(255,255,255,0.08))',
                        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                      }}>
                        <code style={{ fontSize: 12 }}>
                          {c.candidate_name}{c.candidate_version ? `==${c.candidate_version}` : ''}
                        </code>
                        {c.registry && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>({c.registry})</span>}
                        <span className={`badge ${c.passed_sandbox ? 'badge-green' : 'badge-red'}`} style={{ fontSize: 10 }}>
                          sandbox {c.passed_sandbox ? '✓' : '✗'}
                        </span>
                        <span className={`badge ${c.passed_governance ? 'badge-green' : 'badge-gray'}`} style={{ fontSize: 10 }}>
                          governance {c.passed_governance ? '✓' : '✗'}
                        </span>
                        {c.governance_proposal_id && (
                          <code style={{ fontSize: 10, color: 'var(--text-muted)' }}>{c.governance_proposal_id}</code>
                        )}
                        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>{fmtDate(c.created_at)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}

            {job.state === 'governance_pending' && (
              <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border, rgba(255,255,255,0.08))' }}>
                {job.governance_proposal_db_id == null ? (
                  <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
                    Awaiting governance proposal — approve/reject will appear once it is raised.
                  </div>
                ) : (
                  <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <button
                        className="btn btn-primary btn-sm"
                        disabled={voteBusy === job.id}
                        onClick={() => handleVote(job, 'yes')}
                      >
                        {voteBusy === job.id ? 'Working…' : 'Approve'}
                      </button>
                      <button
                        className="btn btn-secondary btn-sm"
                        disabled={voteBusy === job.id}
                        onClick={() => handleVote(job, 'no')}
                      >
                        Reject
                      </button>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        Casts your operator vote on proposal <code>{job.governance_proposal_id}</code>.
                      </span>
                    </div>
                    {voteErr[job.id] && (
                      <div style={{ fontSize: 11.5, color: '#f87171', marginTop: 6 }}>{voteErr[job.id]}</div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        ))
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

function guardrailActionBadge(action: string): string {
  if (action === 'block') return 'badge-red'
  if (action === 'flag') return 'badge-yellow'
  if (action === 'redact') return 'badge-blue'
  return 'badge-gray'
}

function GuardrailEventCard({ ev }: { ev: GuardrailEvent }) {
  return (
    <div className="glass-card" style={{ padding: '14px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
        <span className={`badge ${guardrailActionBadge(ev.action)}`}>{ev.action}</span>
        <span className="badge badge-gray">{ev.stage}</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{ev.classifier}</span>
        {ev.degraded && <span className="badge badge-yellow">degraded</span>}
        <span style={{ fontSize: 11.5, fontFamily: 'monospace', color: 'var(--text-muted)' }}>
          {Math.round((ev.confidence ?? 0) * 100)}% conf
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>{fmtDate(ev.created_at)}</span>
      </div>
      <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: ev.patterns.length ? 6 : 0 }}>{ev.reason}</p>
      {ev.patterns.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {ev.patterns.filter(Boolean).map((p, i) => (
            <span key={i} style={{ fontSize: 10.5, fontFamily: 'monospace', padding: '1px 6px', borderRadius: 4, background: 'rgba(255,255,255,0.06)', color: 'var(--text-muted)' }}>{p}</span>
          ))}
        </div>
      )}
    </div>
  )
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
