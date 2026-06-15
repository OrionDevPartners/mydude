import { useState, useCallback, useEffect, useRef } from 'react'
import {
  listEvolutionComponents, getEvolutionComponent, getEvolutionComponentStatus,
  getEvolutionLoopStatus, startEvolutionLoop, stopEvolutionLoop,
  triggerEvolutionTrial, seedEvolutionThesis,
  getEvolutionStallSettings, setEvolutionStallSettings, getEvolutionComponentStalls,
  CognitionComponent, ComponentDetail, EvolutionThesis, EvolutionCycleLog,
  ComponentStalls,
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Badge, Empty, Collapsible } from '@/components/ui'
import { GlassStatCard, GlassSection } from '@/components/glass'
import { fmtDate } from '@/lib/utils'
import {
  FlaskConical, ChevronRight, ChevronLeft, Play, Square, RotateCcw,
  ShieldCheck, CheckCircle, AlertCircle, Clock, Cpu, Activity, GitBranch,
  Zap, AlertTriangle, SlidersHorizontal, Save, Sparkles, XCircle,
} from 'lucide-react'
import type { LlmProposal } from '@/lib/api'

const STATUS_COLOR: Record<string, string> = {
  proposed: 'blue',
  testing: 'orange',
  awaiting_consensus: 'orange',
  awaiting_human_approval: 'yellow',
  promoted: 'green',
  rejected: 'red',
  stalled: 'red',
  error: 'red',
}

const LOOP_COLOR: Record<string, string> = {
  idle: 'gray',
  running: 'green',
  paused: 'orange',
  error: 'red',
}

const OUTCOME_COLOR: Record<string, string> = {
  promoted: 'green',
  rejected: 'red',
  stalled: 'red',
  error: 'red',
  pass: 'green',
  fail: 'red',
}

function pct(n: number | null | undefined) {
  if (n === null || n === undefined) return '—'
  return (n * 100).toFixed(1) + '%'
}

function scoreColor(n: number | null | undefined) {
  if (n === null || n === undefined) return 'var(--text-muted)'
  if (n >= 0.6) return '#34d399'
  if (n >= 0.35) return '#fbbf24'
  return '#f87171'
}

// ---------------------------------------------------------------------------
// Iteration timeline
// ---------------------------------------------------------------------------
function IterationRow({ iter }: { iter: ReturnType<typeof Object.create> }) {
  const icon = iter.outcome === 'pass'
    ? <CheckCircle size={13} color="#34d399" />
    : iter.outcome === 'error' || iter.outcome === 'fail'
    ? <AlertCircle size={13} color="#f87171" />
    : <Clock size={13} />
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '7px 0', borderBottom: '1px solid var(--border)', fontSize: 12.5 }}>
      {icon}
      <Badge color={OUTCOME_COLOR[iter.outcome] || 'gray'}>{iter.outcome}</Badge>
      <span style={{ color: 'var(--text-muted)' }}>iter #{iter.iteration_no}</span>
      <span style={{ color: scoreColor(iter.composite_score), fontWeight: 600 }}>
        {pct(iter.composite_score)}
      </span>
      <span style={{ color: 'var(--text-muted)' }}>
        CS {iter.compliance_score?.toFixed(0) ?? '—'}
      </span>
      <span style={{ color: 'var(--text-muted)' }}>
        HR {iter.hallucination_risk?.toFixed(3) ?? '—'}
      </span>
      <Badge color="gray" style={{ marginLeft: 'auto', fontSize: 10 }}>{iter.sandbox_label}</Badge>
      {iter.error && (
        <span style={{ color: '#f87171', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 11 }} title={iter.error}>
          {iter.error}
        </span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Thesis origin (LLM swarm vs heuristic) + governed LLM proposal record
// ---------------------------------------------------------------------------
function SourceBadge({ source }: { source?: string }) {
  if (source === 'llm_swarm') {
    return (
      <Badge color="purple" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <Sparkles size={11} /> LLM swarm
      </Badge>
    )
  }
  if (source === 'manual') return <Badge color="gray">manual</Badge>
  return <Badge color="gray">heuristic</Badge>
}

const LLM_PROPOSAL_STATUS_COLOR: Record<string, string> = {
  accepted: 'green',
  discarded: 'red',
  no_signal: 'orange',
  unavailable: 'gray',
  error: 'red',
}

const LLM_TIER_COLOR: Record<string, string> = {
  LOW: 'green',
  MEDIUM: 'orange',
  HIGH: 'red',
  CRITICAL: 'red',
}

// Render the governed LLM-swarm proposal record captured for this cycle: whether
// the swarm's proposal was accepted into the candidate pool or discarded by the
// compliance / hallucination gate, with the governance scores and rationale.
function LlmProposalBlock({ proposal }: { proposal: LlmProposal }) {
  const status = proposal.status || 'unavailable'
  // 'unavailable' just means no provider / feature off — not an AI proposal at
  // all, so there is nothing meaningful to surface for operators.
  if (status === 'unavailable') return null

  const isAccepted = status === 'accepted'
  const isDiscarded = status === 'discarded'
  const hasScores =
    proposal.compliance_score != null ||
    proposal.hallucination_risk != null ||
    proposal.hallucination_tier != null

  return (
    <div style={{
      marginTop: 8, padding: '9px 11px', borderRadius: 7,
      border: '1px solid var(--border)',
      background: isDiscarded ? 'rgba(231,76,60,0.07)' : 'rgba(155,89,182,0.07)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
        {isAccepted
          ? <Sparkles size={13} color="#9b59b6" />
          : <XCircle size={13} color="#e74c3c" />}
        <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          LLM swarm proposal
        </span>
        <Badge color={LLM_PROPOSAL_STATUS_COLOR[status] || 'gray'}>{status.replace('_', ' ')}</Badge>
        {hasScores && (
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 10, fontSize: 11.5 }}>
            {proposal.compliance_score != null && (
              <span style={{ color: 'var(--text-muted)' }}>
                CS <b style={{ color: 'var(--text-secondary)' }}>{proposal.compliance_score.toFixed(1)}</b>
              </span>
            )}
            {proposal.hallucination_risk != null && (
              <span style={{ color: 'var(--text-muted)' }}>
                HR <b style={{ color: 'var(--text-secondary)' }}>{proposal.hallucination_risk.toFixed(3)}</b>
              </span>
            )}
            {proposal.hallucination_tier && (
              <Badge color={LLM_TIER_COLOR[proposal.hallucination_tier] || 'gray'} style={{ fontSize: 10 }}>
                {proposal.hallucination_tier}
              </Badge>
            )}
          </span>
        )}
      </div>
      {(isAccepted ? proposal.rationale : proposal.reason) && (
        <p style={{
          fontSize: 12, margin: '7px 0 0', lineHeight: 1.5,
          color: isDiscarded ? '#e67e73' : 'var(--text-muted)',
        }}>
          {isDiscarded && <b>Discarded: </b>}
          {isAccepted ? proposal.rationale : proposal.reason}
        </p>
      )}
      {isAccepted && proposal.directive && (
        <Collapsible title={<span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>Swarm-synthesized directive</span>}>
          <pre style={{
            whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 11, lineHeight: 1.5,
            background: 'rgba(0,0,0,0.25)', padding: 10, borderRadius: 6, marginTop: 6,
            color: 'var(--text-secondary, #cbd5e1)', maxHeight: 200, overflow: 'auto',
          }}>{proposal.directive}</pre>
        </Collapsible>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Thesis card
// ---------------------------------------------------------------------------
function ThesisCard({ thesis, expanded }: { thesis: EvolutionThesis; expanded?: boolean }) {
  const [open, setOpen] = useState(expanded ?? false)
  const sv = thesis.selection_votes || {}
  const llmProposal = (sv.llm_proposal as LlmProposal | null | undefined) ?? null
  return (
    <Card style={{ padding: '12px 14px', marginBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap', cursor: 'pointer' }} onClick={() => setOpen(o => !o)}>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
          {thesis.branch_cell}
        </span>
        <Badge color={STATUS_COLOR[thesis.status] || 'gray'}>{thesis.status}</Badge>
        <SourceBadge source={sv.source as string | undefined} />
        {thesis.requires_human_gate && <Badge color="orange">human-gate</Badge>}
        {thesis.governance_proposal_id && (
          <Badge color="blue" style={{ fontSize: 10 }}>proposal: {thesis.governance_proposal_id}</Badge>
        )}
        <span style={{ fontSize: 11.5, color: 'var(--text-muted)', marginLeft: 'auto' }}>
          cycle #{thesis.cycle_index} · {thesis.trial_iteration_count} iter
        </span>
        {thesis.test_score !== null && (
          <span style={{ fontSize: 12, fontWeight: 700, color: scoreColor(thesis.test_score) }}>
            {pct(thesis.test_score)}
          </span>
        )}
      </div>
      {thesis.rationale && (
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '6px 0 0', lineHeight: 1.5 }}>
          {thesis.rationale}
        </p>
      )}
      {llmProposal && <LlmProposalBlock proposal={llmProposal} />}
      {open && (
        <div style={{ marginTop: 10 }}>
          {thesis.iterations.length > 0 && (
            <div>
              <div style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                EXPERIMENTAL Trial Iterations
              </div>
              {thesis.iterations.map(i => <IterationRow key={i.id} iter={i} />)}
            </div>
          )}
          <Collapsible title={<span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Thesis payload</span>}>
            <pre style={{
              whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 11, lineHeight: 1.5,
              background: 'rgba(0,0,0,0.25)', padding: 10, borderRadius: 6, marginTop: 6,
              color: 'var(--text-secondary, #cbd5e1)', maxHeight: 200, overflow: 'auto',
            }}>{JSON.stringify(thesis.thesis, null, 2)}</pre>
          </Collapsible>
        </div>
      )}
      <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-muted)' }}>
        created {fmtDate(thesis.created_at)}
        {thesis.stalled_at && <span style={{ color: '#f87171' }}> · stalled {fmtDate(thesis.stalled_at)}</span>}
      </div>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Cycle log row
// ---------------------------------------------------------------------------
function CycleLogRow({ log }: { log: EvolutionCycleLog }) {
  const icon = log.outcome === 'promoted'
    ? <CheckCircle size={13} color="#34d399" />
    : log.outcome === 'rejected'
    ? <AlertCircle size={13} color="#f87171" />
    : <AlertTriangle size={13} color="#fbbf24" />
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', borderBottom: '1px solid var(--border)', fontSize: 12.5 }}>
      {icon}
      <Badge color={OUTCOME_COLOR[log.outcome] || 'gray'}>{log.outcome}</Badge>
      <span style={{ color: 'var(--text-muted)' }}>cycle #{log.cycle_index}</span>
      {log.thesis_id && <span style={{ color: 'var(--text-muted)' }}>thesis #{log.thesis_id}</span>}
      {log.detail && <span style={{ color: 'var(--text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{log.detail}</span>}
      <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', flexShrink: 0 }}>{fmtDate(log.created_at)}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Stall-retry tuning + per-branch-cell stall counts
// ---------------------------------------------------------------------------
function StallTuningSection({ componentId }: { componentId: number }) {
  const { data, loading, error, refetch } = useApi<ComponentStalls>(
    () => getEvolutionComponentStalls(componentId), [componentId])
  const [value, setValue] = useState<string>('')
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)
  const [lookback, setLookback] = useState<number | null>(null)
  const [dflt, setDflt] = useState<number | null>(null)

  // Seed the editor from the authoritative settings endpoint (global value).
  useEffect(() => {
    let alive = true
    getEvolutionStallSettings()
      .then(s => {
        if (!alive) return
        setValue(String(s.max_stall_retries))
        setLookback(s.lookback_cycles)
        setDflt(s.default_max_stall_retries)
      })
      .catch(() => { /* surfaced via the stalls endpoint's max_stall_retries fallback */ })
    return () => { alive = false }
  }, [])

  const handleSave = useCallback(async () => {
    const n = Number(value)
    if (!Number.isInteger(n) || n < 1) {
      setNotice({ type: 'error', msg: 'Retry limit must be a whole number ≥ 1.' })
      return
    }
    setSaving(true); setNotice(null)
    try {
      const res = await setEvolutionStallSettings(n)
      setValue(String(res.max_stall_retries))
      setNotice({ type: 'success', msg: `Stall retry limit saved (${res.max_stall_retries}).` })
      refetch()
    } catch (e: unknown) {
      setNotice({ type: 'error', msg: e instanceof Error ? e.message : 'Failed to save retry limit' })
    } finally { setSaving(false) }
  }, [value, refetch])

  const maxRetries = data?.max_stall_retries ?? null
  const cells = data?.stalled_branch_cells ?? []

  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 8px' }}>
        <SlidersHorizontal size={14} style={{ opacity: 0.7 }} />
        <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Stall Retry Tuning</h3>
      </div>
      <Card style={{ padding: '12px 14px' }}>
        <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: '0 0 10px', lineHeight: 1.5 }}>
          How many times a branch cell may stall within the last{' '}
          <b>{lookback ?? data?.lookback_cycles ?? '—'}</b> cycles before it is hard-deprioritized
          (instead of merely weighted down). Applies to all components.
        </p>
        {notice && <Alert type={notice.type} onClose={() => setNotice(null)}>{notice.msg}</Alert>}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12.5, color: 'var(--text-secondary)' }} htmlFor="max-stall-retries">
            Max stall retries
          </label>
          <input
            id="max-stall-retries"
            type="number"
            min={1}
            step={1}
            value={value}
            onChange={e => setValue(e.target.value)}
            disabled={saving}
            style={{
              width: 80, padding: '6px 8px', fontSize: 13,
              background: 'rgba(0,0,0,0.25)', color: 'var(--text-primary, #e5e7eb)',
              border: '1px solid var(--border)', borderRadius: 6,
            }}
          />
          <button className="btn btn-primary btn-sm" disabled={saving} onClick={handleSave} style={{ gap: 6 }}>
            {saving ? <Spinner size={13} /> : <Save size={13} />} Save
          </button>
          {dflt !== null && (
            <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>default {dflt}</span>
          )}
        </div>

        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
            Recent stalls by branch cell
          </div>
          {loading && !data
            ? <Spinner size={16} />
            : error
            ? <Alert type="error">{error}</Alert>
            : cells.length === 0
            ? <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: 0 }}>
                No branch cells have stalled in the lookback window.
              </p>
            : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {cells.map(cell => (
                  <div key={cell.branch_cell} style={{
                    display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5,
                    padding: '6px 0', borderBottom: '1px solid var(--border)',
                  }}>
                    <AlertTriangle size={13} color={cell.deprioritized ? '#e74c3c' : '#f39c12'} />
                    <code style={{ color: 'var(--text-primary, #e5e7eb)' }}>{cell.branch_cell}</code>
                    <span style={{ color: 'var(--text-muted)' }}>
                      stalled <b>{cell.stall_count}</b>
                      {maxRetries !== null ? ` / ${maxRetries}` : ''} time{cell.stall_count === 1 ? '' : 's'}
                    </span>
                    {cell.deprioritized && (
                      <span style={{ marginLeft: 'auto' }}>
                        <Badge color="red">deprioritized</Badge>
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
        </div>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component detail panel
// ---------------------------------------------------------------------------
function ComponentDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const { data, loading, error, refetch } = useApi<ComponentDetail>(() => getEvolutionComponent(id), [id])
  const [notice, setNotice] = useState<{ type: 'success' | 'error' | 'info'; msg: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const sigRef = useRef<string | null>(null)

  const stopPoll = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  // Keep a change signature of the rendered data so the poller can tell when a
  // full refetch is actually warranted (avoids hammering the heavy endpoint).
  useEffect(() => {
    if (!data) return
    const c = data.component
    const active = data.theses.find(t =>
      ['proposed', 'testing', 'awaiting_consensus', 'awaiting_human_approval'].includes(t.status))
    sigRef.current = [
      c.loop_state, c.cycle_count, c.promoted_theses, c.total_theses,
      active?.id ?? '', active?.status ?? '', active?.iterations.length ?? 0,
      data.cycle_logs[0]?.id ?? '',
    ].join('|')
    setLastUpdated(new Date())
  }, [data])

  // Poll the lightweight status endpoint every ~3s while the loop is running so
  // operators watch cycles land live. Only triggers a full refetch on change.
  const loopState = data?.component.loop_state
  const threadAlive = data?.component.thread_alive
  useEffect(() => {
    const running = loopState === 'running' || threadAlive === true
    stopPoll()
    if (!running) return
    pollRef.current = setInterval(async () => {
      try {
        const s = await getEvolutionComponentStatus(id)
        setLastUpdated(new Date())
        const sig = [
          s.loop_state, s.cycle_count, s.promoted_theses, s.total_theses,
          s.active_thesis_id ?? '', s.active_thesis_status ?? '', s.active_thesis_iterations,
          s.latest_cycle_log_id ?? '',
        ].join('|')
        const stillRunning = s.loop_state === 'running' || s.thread_alive
        if (sig !== sigRef.current || !stillRunning) {
          sigRef.current = sig
          refetch()
        }
      } catch { /* transient; keep polling */ }
    }, 3000)
    return stopPoll
  }, [id, loopState, threadAlive, refetch, stopPoll])

  const handleStart = useCallback(async () => {
    setBusy(true); setNotice(null)
    try {
      const res = await startEvolutionLoop(id)
      setNotice({ type: 'success', msg: res.already_running ? 'Loop was already running.' : 'Evolution loop started.' })
      refetch()
    } catch (e: unknown) {
      setNotice({ type: 'error', msg: e instanceof Error ? e.message : 'Failed to start loop' })
    } finally { setBusy(false) }
  }, [id, refetch])

  const handleStop = useCallback(async () => {
    setBusy(true); setNotice(null)
    try {
      await stopEvolutionLoop(id)
      setNotice({ type: 'info', msg: 'Loop stop signal sent.' })
      refetch()
    } catch (e: unknown) {
      setNotice({ type: 'error', msg: e instanceof Error ? e.message : 'Failed to stop loop' })
    } finally { setBusy(false) }
  }, [id, refetch])

  const handleTrial = useCallback(async () => {
    setBusy(true); setNotice(null)
    try {
      const res = await triggerEvolutionTrial(id)
      setNotice({ type: 'success', msg: `Trial complete — outcome: ${res.outcome}` })
      refetch()
    } catch (e: unknown) {
      setNotice({ type: 'error', msg: e instanceof Error ? e.message : 'Trial failed' })
    } finally { setBusy(false) }
  }, [id, refetch])

  if (loading && !data) return <div style={{ padding: 40, textAlign: 'center' }}><Spinner size={24} /></div>
  if (error && !data) return <Alert type="error">{error}</Alert>
  if (!data) return <Empty message="Component not found." />

  const c = data.component
  const isRunning = c.loop_state === 'running' || c.thread_alive
  const activeTheses = data.theses.filter(t => ['proposed', 'testing', 'awaiting_consensus', 'awaiting_human_approval'].includes(t.status))
  const promotedTheses = data.theses.filter(t => t.status === 'promoted')
  const rejectedTheses = data.theses.filter(t => ['rejected', 'stalled'].includes(t.status))

  return (
    <div>
      <button className="btn btn-ghost btn-sm" onClick={onBack} style={{ gap: 6, marginBottom: 14 }}>
        <ChevronLeft size={15} /> All components
      </button>

      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <h2 style={{ fontSize: 18, fontWeight: 800, margin: 0 }}>{c.name}</h2>
            <Badge color="gray">{c.component_type}</Badge>
            <Badge color={LOOP_COLOR[c.loop_state] || 'gray'}>{c.loop_state}</Badge>
            {c.thread_alive && <Badge color="green">thread alive</Badge>}
            {isRunning && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11.5, color: '#34d399' }}>
                <Spinner size={11} /> live
              </span>
            )}
          </div>
          {lastUpdated && (
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
              {isRunning ? 'Auto-refreshing every 3s · ' : ''}last updated {lastUpdated.toLocaleTimeString()}
            </div>
          )}
          {c.description && (
            <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '6px 0 0', lineHeight: 1.5 }}>{c.description}</p>
          )}
          <div style={{ display: 'flex', gap: 16, marginTop: 10, fontSize: 12, color: 'var(--text-muted)' }}>
            <span><b>{c.cycle_count}</b> cycles</span>
            <span><b>{c.total_theses}</b> theses</span>
            <span><b>{c.promoted_theses}</b> promoted</span>
            {c.last_cycle_at && <span>last cycle {fmtDate(c.last_cycle_at)}</span>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          {!isRunning ? (
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={handleStart} style={{ gap: 6 }}>
              <Play size={13} /> Start loop
            </button>
          ) : (
            <button className="btn btn-ghost btn-sm" disabled={busy} onClick={handleStop} style={{ gap: 6 }}>
              <Square size={13} /> Stop loop
            </button>
          )}
          <button className="btn btn-ghost btn-sm" disabled={busy || isRunning} onClick={handleTrial} style={{ gap: 6 }} title={isRunning ? 'Stop loop first to run manual trial' : ''}>
            {busy ? <Spinner size={13} /> : <RotateCcw size={13} />}
            Run trial
          </button>
        </div>
      </div>

      {notice && <Alert type={notice.type} onClose={() => setNotice(null)}>{notice.msg}</Alert>}

      {/* Active thesis */}
      {activeTheses.length > 0 && (
        <div style={{ marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 8px' }}>
            <Activity size={14} style={{ opacity: 0.7 }} />
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Active Thesis (EXPERIMENTAL)</h3>
          </div>
          {activeTheses.map(t => <ThesisCard key={t.id} thesis={t} expanded />)}
        </div>
      )}

      {/* Current truth */}
      <div style={{ marginBottom: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 8px' }}>
          <ShieldCheck size={14} style={{ opacity: 0.7 }} />
          <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Current Truth (Champion)</h3>
        </div>
        <Card style={{ padding: '12px 14px' }}>
          {c.truth_version_id !== null && (
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginBottom: 6 }}>
              Linked prompt version: <b>#{c.truth_version_id}</b>
            </div>
          )}
          <Collapsible title={<span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>Truth snapshot</span>}>
            <pre style={{
              whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 11, lineHeight: 1.5,
              background: 'rgba(0,0,0,0.25)', padding: 10, borderRadius: 6, marginTop: 6,
              color: 'var(--text-secondary, #cbd5e1)', maxHeight: 200, overflow: 'auto',
            }}>{JSON.stringify(c.truth_json, null, 2)}</pre>
          </Collapsible>
        </Card>
      </div>

      {/* Promotion history */}
      {promotedTheses.length > 0 && (
        <div style={{ marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 8px' }}>
            <CheckCircle size={14} style={{ opacity: 0.7 }} />
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Promoted Theses</h3>
          </div>
          {promotedTheses.map(t => <ThesisCard key={t.id} thesis={t} />)}
        </div>
      )}

      {/* Rejected theses */}
      {rejectedTheses.length > 0 && (
        <div style={{ marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 8px' }}>
            <AlertCircle size={14} style={{ opacity: 0.7 }} />
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Rejected / Stalled Theses</h3>
          </div>
          {rejectedTheses.map(t => <ThesisCard key={t.id} thesis={t} />)}
        </div>
      )}

      {/* Stall retry tuning */}
      <StallTuningSection componentId={id} />

      {/* Cycle log */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 8px' }}>
          <GitBranch size={14} style={{ opacity: 0.7 }} />
          <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Cycle Audit Log</h3>
        </div>
        <Card style={{ padding: '6px 14px 10px' }}>
          {data.cycle_logs.length === 0
            ? <p style={{ fontSize: 12.5, color: 'var(--text-muted)', padding: '10px 0', margin: 0 }}>No cycles recorded yet.</p>
            : data.cycle_logs.map(l => <CycleLogRow key={l.id} log={l} />)}
        </Card>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component list
// ---------------------------------------------------------------------------
function ComponentCard({ c, onSelect }: { c: CognitionComponent; onSelect: () => void }) {
  const isRunning = c.loop_state === 'running' || c.thread_alive
  const typeIcon =
    c.component_type === 'prompt_program' ? <Zap size={14} /> :
    c.component_type === 'swarm_config' ? <Cpu size={14} /> :
    <Activity size={14} />

  return (
    <Card style={{ padding: '14px 16px', cursor: 'pointer' }} onClick={onSelect}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 4, flexWrap: 'wrap' }}>
            {typeIcon}
            <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>{c.name}</span>
            <Badge color="gray">{c.component_type}</Badge>
            <Badge color={LOOP_COLOR[c.loop_state] || 'gray'}>{c.loop_state}</Badge>
            {isRunning && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11.5, color: '#34d399' }}>
                <Spinner size={11} /> evolving
              </span>
            )}
          </div>
          {c.description && (
            <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0, lineHeight: 1.4 }}>{c.description}</p>
          )}
          <div style={{ display: 'flex', gap: 14, marginTop: 8, fontSize: 12, color: 'var(--text-muted)' }}>
            <span><b>{c.cycle_count}</b> cycles</span>
            <span><b>{c.total_theses}</b> theses</span>
            <span style={{ color: '#34d399' }}><b>{c.promoted_theses}</b> promoted</span>
            {c.active_thesis && (
              <span style={{ color: '#fbbf24' }}>
                active thesis: {c.active_thesis.branch_cell} ({c.active_thesis.status})
              </span>
            )}
          </div>
        </div>
        <ChevronRight size={17} style={{ opacity: 0.45, flexShrink: 0 }} />
      </div>
    </Card>
  )
}

function ComponentList({ onSelect }: { onSelect: (id: number) => void }) {
  const { data, loading, error } = useApi(() => listEvolutionComponents(), [])
  const [components, setComponents] = useState<CognitionComponent[] | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Seed local state from the initial fetch; the poller takes over from here.
  useEffect(() => {
    if (data) {
      setComponents(data.components)
      setLastUpdated(new Date())
    }
  }, [data])

  // Auto-refresh the overview every ~3s while any component's loop is running so
  // cycle counts, promoted counts and the "evolving" badge update in place.
  // Polling stops as soon as nothing is running.
  const anyRunning = (components ?? []).some(c => c.loop_state === 'running' || c.thread_alive)
  useEffect(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    if (!anyRunning) return
    pollRef.current = setInterval(async () => {
      try {
        const s = await getEvolutionLoopStatus()
        setComponents(s.components)
        setLastUpdated(new Date())
      } catch { /* transient; keep polling */ }
    }, 3000)
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }
  }, [anyRunning])

  if (loading && !components) return <div style={{ padding: 40, textAlign: 'center' }}><Spinner size={24} /></div>
  if (error && !components) return <Alert type="error">{error}</Alert>
  if (!components || components.length === 0) return (
    <Empty
      message="No cognition components registered yet. They are seeded at application startup."
      icon={<FlaskConical size={28} />}
    />
  )

  const running = components.filter(c => c.loop_state === 'running' || c.thread_alive).length
  const totalCycles = components.reduce((s, c) => s + c.cycle_count, 0)
  const totalPromoted = components.reduce((s, c) => s + c.promoted_theses, 0)

  return (
    <div>
      {anyRunning && lastUpdated && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
          <Spinner size={11} />
          <span style={{ color: '#2ecc71' }}>Live</span>
          <span>· auto-refreshing every 3s · last updated {lastUpdated.toLocaleTimeString()}</span>
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 24 }}>
        <GlassStatCard value={components.length} label="Components" icon={<FlaskConical size={16} />} />
        <GlassStatCard value={running} label="Evolving now" icon={<Activity size={16} />} glow={running > 0} />
        <GlassStatCard value={totalCycles} label="Total cycles" icon={<RotateCcw size={16} />} />
        <GlassStatCard value={totalPromoted} label="Promoted theses" icon={<CheckCircle size={16} />} />
      </div>
      <GlassSection title="Cognition components" className="animate-fade-in-up">
        <div style={{ display: 'grid', gap: 10 }}>
          {components.map(c => (
            <ComponentCard key={c.id} c={c} onSelect={() => onSelect(c.id)} />
          ))}
        </div>
      </GlassSection>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export function Evolution() {
  const [selectedId, setSelectedId] = useState<number | null>(null)

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Evolution Loop"
        subtitle="EXPERIMENTAL: each cognition component evolves its edge truth through isolated thesis trials, weighted-debate consensus, and governance-gated promotion. The loop self-perpetuates — every promotion or rejection seeds the next thesis automatically."
      />
      {selectedId !== null
        ? <ComponentDetail id={selectedId} onBack={() => setSelectedId(null)} />
        : <ComponentList onSelect={setSelectedId} />}
    </div>
  )
}
