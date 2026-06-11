import { useState, useEffect, useRef, useCallback } from 'react'
import {
  listPrompts, getPromptDetail, optimizePrompt, getPromptRun,
  promptVersionPromote, promptVersionRollback,
  PromptProgramSummary, PromptProgramDetail, PromptVersionRow, PromptRunRow,
  PromptScoreBreakdown,
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Badge, Empty, Collapsible } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import {
  Sparkles, ChevronRight, ChevronLeft, TrendingUp, ShieldCheck, RotateCcw,
  FlaskConical, CheckCircle, AlertCircle, Clock, Database,
} from 'lucide-react'

const VERSION_COLOR: Record<string, string> = {
  live: 'green', candidate: 'blue', archived: 'gray', failed: 'red',
}
const RUN_COLOR: Record<string, string> = {
  running: 'blue', completed: 'green', failed: 'red', pending: 'gray',
}

function pct(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return (n * 100).toFixed(1) + '%'
}
function delta(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  const v = (n * 100)
  return (v >= 0 ? '+' : '') + v.toFixed(1) + ' pts'
}
function cs(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toFixed(0) + ' / 100'
}
function num(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined) return '—'
  return n.toFixed(digits)
}

// ---------------- Score comparison (candidate vs. live baseline) ----------------
type Dir = 'up' | 'down'  // which direction is "better" for this metric

function MetricRow({ label, live, cand, fmt, better }: {
  label: string
  live: number | null | undefined
  cand: number | null | undefined
  fmt: (n: number | null | undefined) => string
  better: Dir
}) {
  const have = live !== null && live !== undefined && cand !== null && cand !== undefined
  let diff = 0
  let color = 'var(--text-muted)'
  if (have) {
    diff = (cand as number) - (live as number)
    const improved = better === 'up' ? diff > 1e-9 : diff < -1e-9
    const worsened = better === 'up' ? diff < -1e-9 : diff > 1e-9
    color = improved ? '#2ecc71' : worsened ? '#e74c3c' : 'var(--text-muted)'
  }
  return (
    <tr>
      <td style={{ padding: '5px 10px 5px 0', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{label}</td>
      <td style={{ padding: '5px 14px 5px 0', textAlign: 'right', color: 'var(--text-secondary, #cbd5e1)', fontVariantNumeric: 'tabular-nums' }}>{fmt(live)}</td>
      <td style={{ padding: '5px 14px 5px 0', textAlign: 'right', fontWeight: 700, color, fontVariantNumeric: 'tabular-nums' }}>{fmt(cand)}</td>
      <td style={{ padding: '5px 0', textAlign: 'right', color, fontSize: 11.5, fontVariantNumeric: 'tabular-nums' }}>
        {have && Math.abs(diff) > 1e-9 ? (better === 'up' ? (diff > 0 ? '▲' : '▼') : (diff < 0 ? '▲' : '▼')) : ''}
      </td>
    </tr>
  )
}

function ScoreComparison({ v }: { v: PromptVersionRow }) {
  const cand: PromptScoreBreakdown | null | undefined = v.breakdown
  const live: PromptScoreBreakdown | null | undefined = v.base_breakdown
  // Composite scores are always present on a scored candidate (base_score is the
  // same-run live baseline); the component breakdown is present for runs created
  // after breakdown capture shipped.
  const haveScores = v.score !== null && v.score !== undefined
  const haveBreakdown = !!cand
  if (!haveScores && !haveBreakdown) return null

  return (
    <div style={{
      marginTop: 12, padding: '10px 12px', borderRadius: 8,
      background: 'rgba(0,0,0,0.18)', border: '1px solid var(--border)',
    }}>
      <div style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.4 }}>
        Live vs. candidate
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
        <thead>
          <tr style={{ color: 'var(--text-muted)', fontSize: 11 }}>
            <th style={{ textAlign: 'left', padding: '0 10px 4px 0', fontWeight: 600 }}>Metric</th>
            <th style={{ textAlign: 'right', padding: '0 14px 4px 0', fontWeight: 600 }}>Live</th>
            <th style={{ textAlign: 'right', padding: '0 14px 4px 0', fontWeight: 600 }}>Candidate</th>
            <th style={{ width: 16 }} />
          </tr>
        </thead>
        <tbody>
          <MetricRow label="Composite score" live={v.base_score} cand={v.score} fmt={pct} better="up" />
          {haveBreakdown && <>
            <MetricRow label="Section coverage" live={live?.format_fraction} cand={cand?.format_fraction} fmt={pct} better="up" />
            <MetricRow label="Compliance score" live={live?.compliance_score} cand={cand?.compliance_score} fmt={cs} better="up" />
            <MetricRow label="Hallucination risk" live={live?.hallucination_risk} cand={cand?.hallucination_risk} fmt={(n) => num(n, 2)} better="down" />
          </>}
        </tbody>
      </table>
      {haveBreakdown && cand && cand.missing_sections && cand.missing_sections.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 11.5, color: '#e0a458' }}>
          Candidate still missing section{cand.missing_sections.length === 1 ? '' : 's'}: {cand.missing_sections.join(', ')}
        </div>
      )}
      {haveBreakdown && cand && (
        <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
          Scored on {cand.n} held-out example{cand.n === 1 ? '' : 's'} from the same run.
        </div>
      )}
      {!haveBreakdown && haveScores && (
        <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-muted)' }}>
          Metric breakdown unavailable for this version — re-optimize to capture the per-component split.
        </div>
      )}
    </div>
  )
}

// ---------------- Program list ----------------
function ProgramList({ onSelect }: { onSelect: (name: string) => void }) {
  const { data, loading, error } = useApi(() => listPrompts(), [])

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><Spinner size={24} /></div>
  if (error) return <Alert type="error">{error}</Alert>
  if (!data || data.programs.length === 0) return <Empty message="No optimizable prompt programs registered yet." icon={<Sparkles size={28} />} />

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {data.programs.map((p: PromptProgramSummary) => {
        const ready = p.usable_trace_count >= data.min_traces
        return (
          <Card key={p.id} style={{ padding: '16px 18px', cursor: 'pointer' }}>
            <div onClick={() => onSelect(p.name)} style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
                  <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>{p.name}</span>
                  <Badge color="gray">{p.signature_name}</Badge>
                  {p.live_version_no !== null && <Badge color="green">live v{p.live_version_no}</Badge>}
                </div>
                <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 }}>{p.description}</p>
                <div style={{ display: 'flex', gap: 16, marginTop: 10, fontSize: 12, color: 'var(--text-muted)' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <Database size={13} /> {p.usable_trace_count} usable trace{p.usable_trace_count === 1 ? '' : 's'}
                  </span>
                  <span>{p.version_count} version{p.version_count === 1 ? '' : 's'}</span>
                  <span style={{ color: ready ? 'var(--green, #2ecc71)' : 'var(--text-muted)' }}>
                    {ready ? 'ready to optimize' : `needs ${data.min_traces} traces`}
                  </span>
                </div>
              </div>
              <ChevronRight size={18} style={{ opacity: 0.5, flexShrink: 0 }} />
            </div>
          </Card>
        )
      })}
    </div>
  )
}

// ---------------- Version row ----------------
function VersionCard({ v, onAction, busy }: {
  v: PromptVersionRow
  onAction: (kind: 'promote' | 'rollback', v: PromptVersionRow) => void
  busy: boolean
}) {
  return (
    <Card style={{ padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, fontWeight: 700 }}>v{v.version_no}</span>
        <Badge color={VERSION_COLOR[v.status] || 'gray'}>{v.status}</Badge>
        {v.optimizer && <Badge color="gray">{v.optimizer}</Badge>}
        {v.ever_live && v.status !== 'live' && <Badge color="orange">was live</Badge>}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
          score {pct(v.score)} {v.delta !== null && <span style={{ color: (v.delta ?? 0) >= 0 ? '#2ecc71' : '#e74c3c' }}>({delta(v.delta)})</span>}
        </span>
      </div>
      {(v.status === 'candidate' || v.breakdown) && <ScoreComparison v={v} />}
      <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        {v.status === 'candidate' && (
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => onAction('promote', v)} style={{ gap: 6 }}>
            <ShieldCheck size={13} /> Propose promotion
          </button>
        )}
        {v.ever_live && v.status !== 'live' && (
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onAction('rollback', v)} style={{ gap: 6 }}>
            <RotateCcw size={13} /> Rollback to this
          </button>
        )}
        <span style={{ fontSize: 11.5, color: 'var(--text-muted)', marginLeft: 'auto' }}>
          {v.promoted_at ? `promoted ${fmtDate(v.promoted_at)}` : `created ${fmtDate(v.created_at)}`}
        </span>
      </div>
      <div style={{ marginTop: 12 }}>
        <Collapsible title={<span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>Instructions</span>}>
          <pre style={{
            whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12, lineHeight: 1.55,
            background: 'rgba(0,0,0,0.25)', padding: 12, borderRadius: 8, margin: '8px 0 0',
            color: 'var(--text-secondary, #cbd5e1)', maxHeight: 320, overflow: 'auto',
          }}>{v.instructions}</pre>
        </Collapsible>
      </div>
    </Card>
  )
}

// ---------------- Run row ----------------
function RunRow({ r }: { r: PromptRunRow }) {
  const icon = r.status === 'completed' ? <CheckCircle size={14} color="#2ecc71" />
    : r.status === 'failed' ? <AlertCircle size={14} color="#e74c3c" />
    : r.status === 'running' ? <Spinner size={13} /> : <Clock size={14} />
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border)', fontSize: 12.5 }}>
      {icon}
      <Badge color={RUN_COLOR[r.status] || 'gray'}>{r.status}</Badge>
      {r.optimizer && <Badge color="gray">{r.optimizer}</Badge>}
      <span style={{ color: 'var(--text-muted)' }}>trainset {r.trainset_size ?? '—'}</span>
      <span style={{ color: 'var(--text-muted)' }}>base {pct(r.base_score)} → best {pct(r.best_score)}</span>
      {r.error && <span style={{ color: '#e74c3c', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.error}>{r.error}</span>}
      <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>{fmtDate(r.created_at)}</span>
    </div>
  )
}

// ---------------- Program detail ----------------
function ProgramDetail({ name, onBack }: { name: string; onBack: () => void }) {
  const { data, loading, error, refetch } = useApi<PromptProgramDetail>(() => getPromptDetail(name), [name])
  const [notice, setNotice] = useState<{ type: 'info' | 'error' | 'success'; msg: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [activeRunId, setActiveRunId] = useState<number | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Detect an already-running optimization on load so polling resumes after refresh.
  useEffect(() => {
    if (data) {
      const running = data.runs.find(r => r.status === 'running')
      if (running) setActiveRunId(running.id)
    }
  }, [data])

  const stopPoll = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  useEffect(() => {
    if (activeRunId === null) return
    stopPoll()
    pollRef.current = setInterval(async () => {
      try {
        const run = await getPromptRun(activeRunId)
        if (run.status !== 'running') {
          stopPoll()
          setActiveRunId(null)
          setNotice(run.status === 'completed'
            ? { type: 'success', msg: `Optimization complete — best score ${pct(run.best_score)}. New candidate version(s) ready for review.` }
            : { type: 'error', msg: `Optimization failed: ${run.error || 'unknown error'}` })
          refetch()
        }
      } catch { /* transient; keep polling */ }
    }, 2500)
    return stopPoll
  }, [activeRunId, stopPoll, refetch])

  async function runOptimize() {
    setBusy(true); setNotice(null)
    try {
      const res = await optimizePrompt(name)
      setActiveRunId(res.run_id)
      setNotice({ type: 'info', msg: 'Optimization started — running MIPROv2 then GEPA in the background. This can take a few minutes.' })
      refetch()
    } catch (e: unknown) {
      setNotice({ type: 'error', msg: e instanceof Error ? e.message : 'Failed to start optimization' })
    } finally { setBusy(false) }
  }

  async function onAction(kind: 'promote' | 'rollback', v: PromptVersionRow) {
    setBusy(true); setNotice(null)
    try {
      if (kind === 'promote') {
        const res = await promptVersionPromote(v.id)
        setNotice({ type: 'success', msg: `${res.message} (proposal ${res.proposal_id})` })
      } else {
        await promptVersionRollback(v.id)
        setNotice({ type: 'success', msg: `Rolled back — v${v.version_no} is now live (audited).` })
      }
      refetch()
    } catch (e: unknown) {
      setNotice({ type: 'error', msg: e instanceof Error ? e.message : 'Action failed' })
    } finally { setBusy(false) }
  }

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><Spinner size={24} /></div>
  if (error) return <Alert type="error">{error}</Alert>
  if (!data) return <Empty message="Program not found." />

  const p = data.program
  const optimizing = activeRunId !== null

  return (
    <div>
      <button className="btn btn-ghost btn-sm" onClick={onBack} style={{ gap: 6, marginBottom: 14 }}>
        <ChevronLeft size={15} /> All programs
      </button>

      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h2 style={{ fontSize: 18, fontWeight: 800, margin: 0 }}>{p.name}</h2>
            {p.live_version_no !== null && <Badge color="green">live v{p.live_version_no}</Badge>}
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '6px 0 0', lineHeight: 1.5 }}>{p.description}</p>
        </div>
        <button className="btn btn-primary" disabled={busy || optimizing} onClick={runOptimize} style={{ gap: 7 }}>
          {optimizing ? <Spinner size={14} /> : <FlaskConical size={15} />}
          {optimizing ? 'Optimizing…' : 'Optimize prompt'}
        </button>
      </div>

      {notice && <Alert type={notice.type === 'success' ? 'success' : notice.type} onClose={() => setNotice(null)}>{notice.msg}</Alert>}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '18px 0 10px' }}>
        <TrendingUp size={15} style={{ opacity: 0.7 }} />
        <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Versions</h3>
      </div>
      <div style={{ display: 'grid', gap: 10 }}>
        {data.versions.map(v => <VersionCard key={v.id} v={v} onAction={onAction} busy={busy} />)}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '24px 0 6px' }}>
        <FlaskConical size={15} style={{ opacity: 0.7 }} />
        <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Optimization runs</h3>
      </div>
      <Card style={{ padding: '6px 16px 12px' }}>
        {data.runs.length === 0
          ? <p style={{ fontSize: 12.5, color: 'var(--text-muted)', padding: '12px 0', margin: 0 }}>No optimization runs yet.</p>
          : data.runs.map(r => <RunRow key={r.id} r={r} />)}
      </Card>
    </div>
  )
}

// ---------------- Page ----------------
export function PromptEngine() {
  const [selected, setSelected] = useState<string | null>(null)

  return (
    <div>
      <PageHeader
        title="Prompt Engine"
        subtitle="Self-evolving, governance-gated prompt optimization. Evolved prompts go live only through the approval gate; rollback is one click and audited."
      />
      {selected
        ? <ProgramDetail name={selected} onBack={() => setSelected(null)} />
        : <ProgramList onSelect={setSelected} />}
    </div>
  )
}
