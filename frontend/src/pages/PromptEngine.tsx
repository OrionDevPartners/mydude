import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import {
  listPrompts, getPromptDetail, optimizePrompt, getPromptRun,
  promptVersionPromote, promptVersionRollback,
  PromptProgramSummary, PromptProgramDetail, PromptVersionRow, PromptRunRow,
  PromptScoreBreakdown, PromptWorstExample,
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Badge, Empty, Collapsible } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import {
  Sparkles, ChevronRight, ChevronLeft, TrendingUp, ShieldCheck, RotateCcw,
  FlaskConical, CheckCircle, AlertCircle, Clock, Database, GitCompare,
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

// ---------------- Line-level diff (live vs. candidate instructions) ----------------
type DiffLine = { type: 'same' | 'add' | 'del'; text: string }

// Longest-common-subsequence line diff. Prompt instructions are at most a few
// hundred lines, so the O(n*m) table is cheap and keeps the diff stable/minimal.
function diffLines(oldText: string, newText: string): DiffLine[] {
  const a = oldText.split('\n')
  const b = newText.split('\n')
  const n = a.length, m = b.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const out: DiffLine[] = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ type: 'same', text: a[i] }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ type: 'del', text: a[i] }); i++ }
    else { out.push({ type: 'add', text: b[j] }); j++ }
  }
  while (i < n) { out.push({ type: 'del', text: a[i] }); i++ }
  while (j < m) { out.push({ type: 'add', text: b[j] }); j++ }
  return out
}

function DiffView({ live, candidate }: { live: string; candidate: string }) {
  const lines = useMemo(() => diffLines(live, candidate), [live, candidate])
  const added = lines.reduce((c, l) => c + (l.type === 'add' ? 1 : 0), 0)
  const removed = lines.reduce((c, l) => c + (l.type === 'del' ? 1 : 0), 0)

  if (added === 0 && removed === 0) {
    return (
      <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '8px 0 0' }}>
        No wording changes — the candidate instructions are identical to the live version.
      </div>
    )
  }

  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: 'flex', gap: 12, fontSize: 11.5, marginBottom: 6, fontVariantNumeric: 'tabular-nums' }}>
        <span style={{ color: '#2ecc71' }}>+{added} added</span>
        <span style={{ color: '#e74c3c' }}>−{removed} removed</span>
      </div>
      <pre style={{
        whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12, lineHeight: 1.6,
        background: 'rgba(0,0,0,0.25)', padding: '8px 0', borderRadius: 8, margin: 0,
        maxHeight: 360, overflow: 'auto',
      }}>
        {lines.map((l, idx) => {
          const sign = l.type === 'add' ? '+' : l.type === 'del' ? '−' : '\u00A0'
          const color = l.type === 'add' ? '#7ee2a8' : l.type === 'del' ? '#f0a0a0' : 'var(--text-secondary, #cbd5e1)'
          const bg = l.type === 'add' ? 'rgba(46,204,113,0.12)' : l.type === 'del' ? 'rgba(231,76,60,0.12)' : 'transparent'
          return (
            <div key={idx} style={{ display: 'flex', gap: 8, padding: '0 12px', background: bg, color }}>
              <span style={{ userSelect: 'none', opacity: 0.7, flexShrink: 0 }}>{sign}</span>
              <span style={{ flex: 1 }}>{l.text || '\u00A0'}</span>
            </div>
          )
        })}
      </pre>
    </div>
  )
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

function WorstExampleRow({ ex, idx }: { ex: PromptWorstExample; idx: number }) {
  return (
    <div style={{
      marginTop: 8, padding: '8px 10px', borderRadius: 6,
      background: 'rgba(0,0,0,0.22)', border: '1px solid var(--border)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 11.5, color: 'var(--text-muted)' }}>
        <span style={{ fontWeight: 700, color: 'var(--text-secondary, #cbd5e1)' }}>#{idx + 1}</span>
        <span>score <b style={{ color: 'var(--text-secondary, #cbd5e1)' }}>{pct(ex.score)}</b></span>
        <span>coverage {pct(ex.format_fraction)}</span>
        <span>compliance {cs(ex.compliance_score)}</span>
        <span>HR {num(ex.hallucination_risk, 2)}</span>
      </div>
      {ex.missing_sections && ex.missing_sections.length > 0 && (
        <div style={{ marginTop: 5, fontSize: 11, color: '#e0a458' }}>
          Missing: {ex.missing_sections.join(', ')}
        </div>
      )}
      {ex.violations && ex.violations.length > 0 && (
        <div style={{ marginTop: 4, fontSize: 11, color: '#e07a7a' }}>
          Violations: {ex.violations.join('; ')}
        </div>
      )}
      <pre style={{
        whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 11, lineHeight: 1.5,
        background: 'rgba(0,0,0,0.3)', padding: 8, borderRadius: 6, margin: '6px 0 0',
        color: 'var(--text-secondary, #cbd5e1)', maxHeight: 180, overflow: 'auto',
      }}>{ex.output ? ex.output : '(empty output — prediction produced no text)'}</pre>
    </div>
  )
}

function WorstExamples({ cand }: { cand: PromptScoreBreakdown }) {
  const examples = cand.worst_examples
  if (!examples || examples.length === 0) return null
  return (
    <div style={{ marginTop: 8 }}>
      <Collapsible title={
        <span style={{ fontSize: 11.5, color: 'var(--text-muted)', fontWeight: 600 }}>
          Lowest-scoring example{examples.length === 1 ? '' : 's'} ({examples.length})
        </span>
      }>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', margin: '2px 0 2px' }}>
          The candidate's worst sample outputs from this run — what dragged the average down.
        </div>
        {examples.map((ex, i) => <WorstExampleRow key={i} ex={ex} idx={i} />)}
      </Collapsible>
    </div>
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
      {haveBreakdown && cand && <WorstExamples cand={cand} />}
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
function VersionCard({ v, onAction, busy, liveInstructions }: {
  v: PromptVersionRow
  onAction: (kind: 'promote' | 'rollback', v: PromptVersionRow) => void
  busy: boolean
  liveInstructions: string | null
}) {
  // Only a candidate is meaningfully diffed against the live baseline; for the
  // live version itself there is nothing to compare against.
  const showDiff = v.status === 'candidate'
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
      {showDiff && (
        <div style={{ marginTop: 8 }}>
          <Collapsible title={
            <span style={{ fontSize: 12.5, color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <GitCompare size={13} /> Diff vs. live
            </span>
          }>
            {liveInstructions === null
              ? (
                <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '8px 0 0' }}>
                  No live baseline yet — promoting this candidate would create the first live version, so there is nothing to diff against.
                </div>
              )
              : <DiffView live={liveInstructions} candidate={v.instructions} />}
          </Collapsible>
        </div>
      )}
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
  const liveInstructions = data.versions.find(x => x.status === 'live')?.instructions ?? null

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
        {data.versions.map(v => (
          <VersionCard key={v.id} v={v} onAction={onAction} busy={busy} liveInstructions={liveInstructions} />
        ))}
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
    <div className="animate-fade-in">
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
