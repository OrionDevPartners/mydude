import { useState, useEffect, useRef, useCallback } from 'react'
import {
  listPrompts, getPromptDetail, optimizePrompt, getPromptRun,
  promptVersionPromote, promptVersionRollback,
  PromptProgramSummary, PromptProgramDetail, PromptVersionRow, PromptRunRow,
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
      <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>{fmtDate(r.started_at)}</span>
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
