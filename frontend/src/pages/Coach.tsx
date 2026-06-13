import { useState, useRef, useEffect } from 'react'
import {
  getCoach, getCoachSignals, ingestCoachText, ingestCoachAudio, computeCoachBehavior, askCoach,
  reflectCoach, setCoachAutoreflect, setCoachStrictPrivate, setInsightOutcome,
  requestCoachAction, confirmCoachAction, rejectCoachAction, purgeCoach,
  CoachData, MoodSignal, CoachInsight, SecretaryAction, MoodProviderConn,
  DeliveryChannel, CoachAskResult,
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, Tabs, Modal, PageHeader, Empty, FormField, Toggle } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import {
  Heart, RefreshCw, Plus, CheckCircle2, XCircle, AlertTriangle, Plug, Sparkles,
  Send, Trash2, ShieldCheck, MessageSquare, Mic, Square, Upload,
} from 'lucide-react'

const SEVERITY_COLOR: Record<string, string> = {
  low: 'badge-gray', medium: 'badge-yellow', high: 'badge-red', critical: 'badge-red', info: 'badge-gray',
}
const INSIGHT_STATUS_COLOR: Record<string, string> = {
  open: 'badge-yellow', acknowledged: 'badge-gray', actioned: 'badge-green', dismissed: 'badge-gray',
}
const ACTION_STATUS_COLOR: Record<string, string> = {
  pending_confirm: 'badge-yellow', needs_provider: 'badge-yellow',
  sent: 'badge-green', rejected: 'badge-gray', failed: 'badge-red',
}

function valenceColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return 'var(--text-muted)'
  if (v > 0.15) return '#3fb950'
  if (v < -0.15) return 'var(--danger, #e94560)'
  return '#e0a800'
}

export function Coach() {
  const [tab, setTab] = useState('Overview')
  const { data, loading, error, refetch } = useApi(getCoach, [])
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  async function action(fn: () => Promise<unknown>, successMsg?: string): Promise<boolean> {
    setWorking(true); setErr(null); setMsg(null)
    try {
      await fn()
      if (successMsg) setMsg(successMsg)
      refetch()
      return true
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error'); return false }
    finally { setWorking(false) }
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Coach"
        subtitle="Empathetic life-coach + secretary — private by design, every outbound action approval-gated"
        actions={
          <button className="btn btn-primary btn-sm" disabled={working}
            onClick={() => action(async () => {
              const r = await reflectCoach()
              if (r.status === 'insufficient_data') setMsg(r.message || 'Not enough signals to reflect yet.')
              else setMsg(`Reflection complete — ${r.insights.length} insight(s) surfaced`)
            })}>
            <Sparkles size={14} /> Reflect now
          </button>
        }
      />
      {msg && <Alert type="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}

      <Tabs tabs={['Overview', 'Signals', 'Coach', 'Approvals', 'Privacy', 'Activity']} active={tab} onChange={setTab} />

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && tab === 'Overview' && <Overview data={data} working={working} action={action} />}
      {data && tab === 'Signals' && <Signals data={data} working={working} action={action} setMsg={setMsg} setErr={setErr} refetch={refetch} />}
      {data && tab === 'Coach' && <CoachTab data={data} working={working} action={action} setErr={setErr} />}
      {data && tab === 'Approvals' && <Approvals data={data} working={working} action={action} setMsg={setMsg} setErr={setErr} refetch={refetch} />}
      {data && tab === 'Privacy' && <Privacy data={data} working={working} action={action} setMsg={setMsg} setErr={setErr} />}
      {data && tab === 'Activity' && <Activity data={data} />}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Overview
// --------------------------------------------------------------------------- //

function MoodProviderCard({ s }: { s: MoodProviderConn }) {
  return (
    <Card style={{ padding: '14px 18px', flex: 1, minWidth: 220 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Plug size={15} style={{ opacity: 0.7 }} />
        <span style={{ fontSize: 14, fontWeight: 700, textTransform: 'capitalize' }}>{s.provider}</span>
        <span className={`badge ${s.connected ? 'badge-green' : 'badge-gray'}`}>
          {s.connected ? 'Connected' : 'Not connected'}
        </span>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>{s.detail}</p>
      {s.source && <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>Source: {s.source}</p>}
    </Card>
  )
}

function DeliveryCard({ s }: { s: DeliveryChannel }) {
  return (
    <Card style={{ padding: '14px 18px', flex: 1, minWidth: 200 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Send size={14} style={{ opacity: 0.7 }} />
        <span style={{ fontSize: 14, fontWeight: 700, textTransform: 'capitalize' }}>{s.channel}</span>
        <span className={`badge ${s.configured ? 'badge-green' : 'badge-gray'}`}>
          {s.configured ? (s.provider || 'Ready') : 'Not configured'}
        </span>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>{s.detail}</p>
    </Card>
  )
}

function MoodTrend({ signals }: { signals: MoodSignal[] }) {
  const withValence = signals.filter(s => s.valence !== null).slice(0, 24).reverse()
  if (withValence.length === 0) {
    return <Empty message="No mood signals yet. Capture one in the Signals tab." icon={<Heart size={32} />} />
  }
  return (
    <Card style={{ padding: '14px 18px' }}>
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Recent mood trend (valence)</div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 80 }}>
        {withValence.map(s => {
          const v = s.valence ?? 0
          const h = Math.max(6, Math.abs(v) * 70)
          return (
            <div key={s.id} title={`${s.label || s.signal_type}: ${v.toFixed(2)} — ${fmtDate(s.observed_at)}`}
              style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', height: '100%' }}>
              <div style={{ height: h, background: valenceColor(v), borderRadius: 2, minWidth: 6 }} />
            </div>
          )
        })}
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
        {withValence.length} signal(s) · green = positive, amber = neutral, red = negative
      </div>
    </Card>
  )
}

function Overview({ data, working, action }: {
  data: CoachData; working: boolean; action: (fn: () => Promise<unknown>, m?: string) => void
}) {
  const hume = data.mood_provider.hume
  const openInsights = data.insights.filter(i => i.status === 'open').length
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        {hume && <MoodProviderCard s={hume} />}
        <DeliveryCard s={data.delivery.email} />
        <DeliveryCard s={data.delivery.sms} />
        <DeliveryCard s={data.delivery.calendar} />
      </div>

      <Card style={{ padding: '14px 18px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>Scheduled reflection</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Periodically surface patterns from recent signals (grounded, local-only).</div>
        </div>
        <Toggle checked={data.autoreflect_enabled} disabled={working}
          onChange={(v) => action(() => setCoachAutoreflect(v), v ? 'Auto-reflection enabled' : 'Auto-reflection disabled')} />
      </Card>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Card style={{ padding: '14px 18px', flex: 1, minWidth: 160 }}>
          <div style={{ fontSize: 24, fontWeight: 800 }}>{data.recent_signals.length}</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Recent signals</div>
        </Card>
        <Card style={{ padding: '14px 18px', flex: 1, minWidth: 160 }}>
          <div style={{ fontSize: 24, fontWeight: 800 }}>{openInsights}</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Open insights</div>
        </Card>
        <Card style={{ padding: '14px 18px', flex: 1, minWidth: 160 }}>
          <div style={{ fontSize: 24, fontWeight: 800, color: data.pending_actions.length ? 'var(--danger, #e94560)' : undefined }}>{data.pending_actions.length}</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Pending approvals</div>
        </Card>
      </div>

      {data.pending_actions.length > 0 && (
        <Alert type="info">
          <AlertTriangle size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
          {data.pending_actions.length} action(s) awaiting your confirmation — review in the Approvals tab.
        </Alert>
      )}

      <MoodTrend signals={data.recent_signals} />
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Signals
// --------------------------------------------------------------------------- //

function Signals({ data, working, action, setMsg, setErr, refetch }: {
  data: CoachData; working: boolean
  action: (fn: () => Promise<unknown>, m?: string) => Promise<boolean>
  setMsg: (s: string | null) => void; setErr: (s: string | null) => void; refetch: () => void
}) {
  const [type, setType] = useState('')
  const [showCapture, setShowCapture] = useState(false)
  const { data: sigData, loading, error, refetch: refetchSignals } = useApi(
    () => getCoachSignals({ signal_type: type || undefined, limit: 200 }), [type])

  function reload() { refetch(); refetchSignals() }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {[['', 'All'], ['emotion', 'Emotion'], ['sentiment', 'Sentiment'], ['behavior', 'Behavior']].map(([val, label]) => (
            <button key={val} className={`btn btn-sm ${type === val ? 'btn-secondary' : 'btn-ghost'}`}
              onClick={() => setType(val)}>{label}</button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn-ghost btn-sm" disabled={working}
            onClick={() => action(async () => {
              const r = await computeCoachBehavior()
              setMsg(`Behavior computed — ${r.written.length} written, ${r.skipped.length} skipped`)
              refetchSignals()
            })}>
            <RefreshCw size={13} /> Compute behavior
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => setShowCapture(true)}>
            <Plus size={13} /> Capture signal
          </button>
        </div>
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 30 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}
      {sigData && (sigData.signals.length === 0
        ? <Empty message="No signals captured yet." icon={<Heart size={32} />} />
        : (
          <div className="glass-card" style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead><tr><th>When</th><th>Type</th><th>Source</th><th>Label</th><th>Valence</th><th>Score</th><th>Summary</th><th></th></tr></thead>
              <tbody>
                {sigData.signals.map(s => (
                  <tr key={s.id}>
                    <td style={{ fontSize: 11, whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>{fmtDate(s.observed_at)}</td>
                    <td><span className="badge badge-gray">{s.signal_type}</span></td>
                    <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{s.source}</td>
                    <td style={{ fontSize: 12 }}>{s.label || '—'}</td>
                    <td style={{ fontSize: 12, fontFamily: 'monospace', color: valenceColor(s.valence) }}>{s.valence === null ? '—' : s.valence.toFixed(2)}</td>
                    <td style={{ fontSize: 12, fontFamily: 'monospace' }}>{s.score === null ? '—' : s.score.toFixed(2)}</td>
                    <td style={{ fontSize: 12, color: 'var(--text-muted)', maxWidth: 240 }}>{s.summary || '—'}{s.private && <span className="badge badge-gray" style={{ marginLeft: 6 }}>private</span>}</td>
                    <td><button className="btn btn-ghost btn-sm" disabled={working}
                      onClick={() => action(async () => { await purgeCoach('PURGE', String(s.id)); reload() }, 'Signal purged')}>
                      <Trash2 size={12} />
                    </button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

      <CaptureModal open={showCapture} projects={data} onClose={() => setShowCapture(false)}
        onSaved={() => { setShowCapture(false); setMsg('Signal captured'); reload() }} onError={setErr} />
    </div>
  )
}

function CaptureModal({ open, projects, onClose, onSaved, onError }: {
  open: boolean; projects: CoachData; onClose: () => void; onSaved: () => void; onError: (e: string) => void
}) {
  const [mode, setMode] = useState<'text' | 'voice'>('text')
  const strictPrivate = projects.strict_private
  return (
    <Modal open={open} onClose={onClose} title="Capture a signal">
      <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
        <button type="button" className={`btn btn-sm ${mode === 'text' ? 'btn-secondary' : 'btn-ghost'}`} onClick={() => setMode('text')}>
          <MessageSquare size={13} /> Text
        </button>
        <button type="button" className={`btn btn-sm ${mode === 'voice' ? 'btn-secondary' : 'btn-ghost'}`} onClick={() => setMode('voice')}>
          <Mic size={13} /> Voice
        </button>
      </div>
      {mode === 'text'
        ? <TextCapture onClose={onClose} onSaved={onSaved} onError={onError} />
        : <VoiceCapture strictPrivate={strictPrivate} onClose={onClose} onSaved={onSaved} onError={onError} />}
    </Modal>
  )
}

function TextCapture({ onClose, onSaved, onError }: {
  onClose: () => void; onSaved: () => void; onError: (e: string) => void
}) {
  const [form, setForm] = useState({ text: '', prefer: 'auto', project_id: '', event_ref: '' })
  const [saving, setSaving] = useState(false)
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setSaving(true)
    try {
      await ingestCoachText({
        text: form.text, prefer: form.prefer,
        project_id: form.project_id || undefined, event_ref: form.event_ref || undefined,
      })
      onSaved(); setForm({ text: '', prefer: 'auto', project_id: '', event_ref: '' })
    } catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }
  return (
    <form onSubmit={submit}>
      <FormField label="How are you feeling? *" hint="Captured locally — analysed for mood/sentiment, stored private by design">
        <textarea className="form-input" rows={3} value={form.text} onChange={e => set('text', e.target.value)} required />
      </FormField>
      <FormField label="Analysis" hint="auto = emotion if Hume connected, else sentiment">
        <select className="form-input" value={form.prefer} onChange={e => set('prefer', e.target.value)}>
          <option value="auto">auto</option>
          <option value="sentiment">sentiment (LLM)</option>
          <option value="emotion">emotion (Hume)</option>
        </select>
      </FormField>
      <FormField label="Link to project ID"><input className="form-input" value={form.project_id} onChange={e => set('project_id', e.target.value)} placeholder="optional" /></FormField>
      <FormField label="Event reference"><input className="form-input" value={form.event_ref} onChange={e => set('event_ref', e.target.value)} placeholder="optional" /></FormField>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Capturing…' : 'Capture'}</button>
      </div>
    </form>
  )
}

function VoiceCapture({ strictPrivate, onClose, onSaved, onError }: {
  strictPrivate: boolean; onClose: () => void; onSaved: () => void; onError: (e: string) => void
}) {
  const [meta, setMeta] = useState({ project_id: '', event_ref: '' })
  const [blob, setBlob] = useState<Blob | null>(null)
  const [filename, setFilename] = useState('recording.webm')
  const [recording, setRecording] = useState(false)
  const [saving, setSaving] = useState(false)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  function set(k: string, v: string) { setMeta(m => ({ ...m, [k]: v })) }

  useEffect(() => {
    if (!blob) { setPreviewUrl(null); return }
    const url = URL.createObjectURL(blob)
    setPreviewUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [blob])

  // Stop any in-flight recording / mic when the modal unmounts.
  useEffect(() => () => {
    try { recorderRef.current?.stop() } catch { /* ignore */ }
    recorderRef.current?.stream?.getTracks().forEach(t => t.stop())
  }, [])

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      chunksRef.current = []
      const rec = new MediaRecorder(stream)
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
      rec.onstop = () => {
        const b = new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' })
        setBlob(b)
        setFilename(`recording.${(rec.mimeType || 'audio/webm').includes('ogg') ? 'ogg' : 'webm'}`)
        stream.getTracks().forEach(t => t.stop())
      }
      recorderRef.current = rec
      rec.start()
      setRecording(true)
    } catch {
      onError('Microphone access was denied or is unavailable. You can upload an audio file instead.')
    }
  }

  function stopRecording() {
    try { recorderRef.current?.stop() } catch { /* ignore */ }
    setRecording(false)
  }

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (f) { setBlob(f); setFilename(f.name) }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!blob) { onError('Record or upload an audio clip first.'); return }
    setSaving(true)
    try {
      await ingestCoachAudio(blob, {
        filename,
        project_id: meta.project_id || undefined,
        event_ref: meta.event_ref || undefined,
      })
      onSaved(); setBlob(null); setMeta({ project_id: '', event_ref: '' })
    } catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }

  return (
    <form onSubmit={submit}>
      {strictPrivate && (
        <Alert type="info">
          <ShieldCheck size={13} style={{ verticalAlign: -2, marginRight: 6 }} />
          Strict-private mode is on. Voice emotion uses the Hume cloud and will be refused — disable strict-private mode to capture voice signals.
        </Alert>
      )}
      <FormField label="Record your voice" hint="A short clip is analysed for vocal emotion (prosody) via Hume. Stored private by design.">
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {!recording
            ? <button type="button" className="btn btn-secondary btn-sm" onClick={startRecording}><Mic size={14} /> Record</button>
            : <button type="button" className="btn btn-primary btn-sm" onClick={stopRecording}><Square size={14} /> Stop</button>}
          {recording && <span style={{ fontSize: 12, color: 'var(--danger, #e94560)' }}>● recording…</span>}
          <label className="btn btn-ghost btn-sm" style={{ cursor: 'pointer', margin: 0 }}>
            <Upload size={14} /> Upload file
            <input type="file" accept="audio/*" onChange={onPick} style={{ display: 'none' }} />
          </label>
        </div>
      </FormField>
      {previewUrl && (
        <div style={{ marginBottom: 12 }}>
          <audio controls src={previewUrl} style={{ width: '100%' }} />
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>{filename}</div>
        </div>
      )}
      <FormField label="Link to project ID"><input className="form-input" value={meta.project_id} onChange={e => set('project_id', e.target.value)} placeholder="optional" /></FormField>
      <FormField label="Event reference"><input className="form-input" value={meta.event_ref} onChange={e => set('event_ref', e.target.value)} placeholder="optional" /></FormField>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="submit" className="btn btn-primary" disabled={saving || !blob || recording}>{saving ? 'Analysing…' : 'Capture voice'}</button>
      </div>
    </form>
  )
}

// --------------------------------------------------------------------------- //
// Coach (ask + insights)
// --------------------------------------------------------------------------- //

function CoachTab({ data, working, action, setErr }: {
  data: CoachData; working: boolean
  action: (fn: () => Promise<unknown>, m?: string) => Promise<boolean>
  setErr: (s: string | null) => void
}) {
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [result, setResult] = useState<CoachAskResult | null>(null)

  async function ask(e: React.FormEvent) {
    e.preventDefault(); if (!question.trim()) return
    setAsking(true); setErr(null); setResult(null)
    try { setResult(await askCoach(question.trim())) }
    catch (err: unknown) { setErr(err instanceof Error ? err.message : 'Error') }
    finally { setAsking(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Card style={{ padding: '14px 18px' }}>
        <form onSubmit={ask}>
          <FormField label="Ask your coach" hint="Answers are grounded in your recorded signals — it will say so when it lacks data">
            <textarea className="form-input" rows={2} value={question} onChange={e => setQuestion(e.target.value)}
              placeholder="e.g. How has my stress trended this month?" />
          </FormField>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button type="submit" className="btn btn-primary btn-sm" disabled={asking || !question.trim()}>
              <MessageSquare size={13} /> {asking ? 'Thinking…' : 'Ask'}
            </button>
          </div>
        </form>

        {result && (
          <div style={{ marginTop: 12, borderTop: '1px solid var(--border, rgba(255,255,255,0.08))', paddingTop: 12 }}>
            {result.status === 'insufficient_data'
              ? <Alert type="info">{result.message}</Alert>
              : (
                <>
                  <div style={{ fontSize: 13, color: 'var(--text-primary)', whiteSpace: 'pre-wrap', lineHeight: 1.55 }}>{result.answer}</div>
                  {result.citations.length > 0 && (
                    <div style={{ marginTop: 10 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', marginBottom: 6 }}>Grounded in</div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        {result.citations.map(c => (
                          <div key={c.ref} style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                            <span className="badge badge-gray" style={{ marginRight: 6 }}>{c.ref}</span>
                            {c.category ? <span style={{ opacity: 0.7 }}>[{c.category}] </span> : null}{c.content}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {data.strict_private && <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}><ShieldCheck size={11} style={{ verticalAlign: -1 }} /> Answered in strict-private mode (self-hosted only)</div>}
                </>
              )}
          </div>
        )}
      </Card>

      <div>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>
          Insights ({data.insights.length})
        </div>
        {data.insights.length === 0
          ? <Empty message="No insights yet. Capture signals, then run Reflect." icon={<Sparkles size={32} />} />
          : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {data.insights.map(i => <InsightCard key={i.id} i={i} working={working} action={action} />)}
            </div>
          )}
      </div>
    </div>
  )
}

function InsightCard({ i, working, action }: {
  i: CoachInsight; working: boolean; action: (fn: () => Promise<unknown>, m?: string) => Promise<boolean>
}) {
  return (
    <Card style={{ padding: '14px 18px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 700 }}>{i.title}</span>
          <span className={`badge ${SEVERITY_COLOR[i.severity] || 'badge-gray'}`}>{i.severity}</span>
          <span className="badge badge-gray">{i.kind}</span>
        </div>
        <span className={`badge ${INSIGHT_STATUS_COLOR[i.status] || 'badge-gray'}`}>{i.status}</span>
      </div>
      {i.detail && <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 8 }}>{i.detail}</p>}
      {i.micro_action && (
        <div style={{ fontSize: 12, color: 'var(--text-primary)', marginBottom: 8 }}>
          <strong style={{ color: 'var(--accent)' }}>Try:</strong> {i.micro_action}
        </div>
      )}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        {(i.citations || []).map(c => (
          <span key={c.ref} className="badge badge-gray" title={c.memory_id || ''}>{c.ref}{c.signal_id ? ` · signal #${c.signal_id}` : ''}</span>
        ))}
        {i.confidence !== null && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>confidence {Math.round((i.confidence ?? 0) * 100)}%</span>}
      </div>
      {i.outcome && <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>Outcome: {i.outcome}</div>}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {['acknowledged', 'actioned', 'dismissed'].map(o => (
          <button key={o} className="btn btn-ghost btn-sm" disabled={working || i.status === o}
            onClick={() => action(() => setInsightOutcome(i.id, o), `Insight marked ${o}`)}>{o}</button>
        ))}
      </div>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Approvals (gated outbound)
// --------------------------------------------------------------------------- //

function Approvals({ data, working, action, setMsg, setErr, refetch }: {
  data: CoachData; working: boolean
  action: (fn: () => Promise<unknown>, m?: string) => Promise<boolean>
  setMsg: (s: string | null) => void; setErr: (s: string | null) => void; refetch: () => void
}) {
  const [showRequest, setShowRequest] = useState(false)
  const [confirmAction, setConfirmAction] = useState<SecretaryAction | null>(null)
  const [confirmText, setConfirmText] = useState('')

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          Emails, texts and bookings are drafted here but never sent until you explicitly confirm.
        </span>
        <button className="btn btn-secondary btn-sm" onClick={() => setShowRequest(true)}><Plus size={13} /> Draft action</button>
      </div>

      {data.actions.length === 0
        ? <Empty message="No drafted actions yet." />
        : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.actions.map(a => (
              <Card key={a.id} style={{ padding: '12px 16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                      <span style={{ fontSize: 13, fontWeight: 700, fontFamily: 'monospace' }}>{a.kind}</span>
                      <span className={`badge ${ACTION_STATUS_COLOR[a.status] || 'badge-gray'}`}>{a.status}</span>
                      {a.channel && <span className="badge badge-gray">{a.channel}</span>}
                    </div>
                    {a.recipient && <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>To: {a.recipient}</div>}
                    {a.subject && <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Subject: {a.subject}</div>}
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', marginTop: 3 }}>{a.body || a.summary || '—'}</div>
                    {a.result_detail && <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>{a.result_detail}</div>}
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>Requested {fmtDate(a.requested_at)}{a.provider ? ` · via ${a.provider}` : ''}</div>
                  </div>
                  {(a.status === 'pending_confirm' || a.status === 'needs_provider') && (
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-danger btn-sm" onClick={() => { setConfirmAction(a); setConfirmText('') }}>
                        <CheckCircle2 size={12} /> Confirm
                      </button>
                      <button className="btn btn-ghost btn-sm" onClick={() => action(() => rejectCoachAction(a.id), 'Action rejected')}>
                        <XCircle size={12} /> Reject
                      </button>
                    </div>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}

      <Modal open={!!confirmAction} onClose={() => setConfirmAction(null)} title="Confirm outbound action">
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
          Type <strong style={{ color: 'var(--accent)' }}>CONFIRM</strong> to send <strong style={{ color: 'var(--text-primary)' }}>{confirmAction?.kind}</strong>
          {confirmAction?.recipient ? <> to <strong>{confirmAction.recipient}</strong></> : null}. This is an outbound, irreversible action.
        </p>
        <input className="form-input" value={confirmText} onChange={e => setConfirmText(e.target.value)} placeholder="CONFIRM" style={{ marginBottom: 14 }} />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={() => setConfirmAction(null)}>Cancel</button>
          <button className="btn btn-danger" disabled={confirmText !== 'CONFIRM' || working}
            onClick={() => confirmAction && action(async () => {
              const r = await confirmCoachAction(confirmAction.id, confirmText)
              if (!r.ok) throw new Error(r.message || 'Confirmation failed')
            }, 'Action sent').then(ok => { if (ok) setConfirmAction(null) })}>
            Send
          </button>
        </div>
      </Modal>

      <RequestActionModal open={showRequest} onClose={() => setShowRequest(false)}
        onSaved={() => { setShowRequest(false); setMsg('Action drafted — confirm to send'); refetch() }} onError={setErr} />
    </div>
  )
}

function RequestActionModal({ open, onClose, onSaved, onError }: {
  open: boolean; onClose: () => void; onSaved: () => void; onError: (e: string) => void
}) {
  const [form, setForm] = useState({ kind: 'draft_email', recipient: '', subject: '', body: '', payload: '', summary: '' })
  const [saving, setSaving] = useState(false)
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }
  const isBooking = form.kind === 'propose_booking'
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setSaving(true)
    try { await requestCoachAction(form); onSaved(); setForm({ kind: 'draft_email', recipient: '', subject: '', body: '', payload: '', summary: '' }) }
    catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }
  return (
    <Modal open={open} onClose={onClose} title="Draft an action">
      <form onSubmit={submit}>
        <FormField label="Kind *">
          <select className="form-input" value={form.kind} onChange={e => set('kind', e.target.value)}>
            <option value="draft_email">draft_email</option>
            <option value="draft_text">draft_text</option>
            <option value="propose_booking">propose_booking</option>
          </select>
        </FormField>
        {isBooking ? (
          <>
            <FormField label="Summary"><input className="form-input" value={form.summary} onChange={e => set('summary', e.target.value)} /></FormField>
            <FormField label="Booking payload (JSON) *" hint='Requires "summary", "start", "end"'>
              <textarea className="form-input" rows={5} style={{ fontFamily: 'monospace', fontSize: 12 }} value={form.payload} onChange={e => set('payload', e.target.value)}
                placeholder='{"summary":"Coffee","start":"2026-06-12T15:00:00Z","end":"2026-06-12T15:30:00Z"}' />
            </FormField>
          </>
        ) : (
          <>
            <FormField label="Recipient *" hint="Email address or phone number"><input className="form-input" value={form.recipient} onChange={e => set('recipient', e.target.value)} /></FormField>
            {form.kind === 'draft_email' && <FormField label="Subject"><input className="form-input" value={form.subject} onChange={e => set('subject', e.target.value)} /></FormField>}
            <FormField label="Message *"><textarea className="form-input" rows={4} value={form.body} onChange={e => set('body', e.target.value)} /></FormField>
          </>
        )}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Draft'}</button>
        </div>
      </form>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Privacy
// --------------------------------------------------------------------------- //

function Privacy({ data, working, action, setMsg }: {
  data: CoachData; working: boolean
  action: (fn: () => Promise<unknown>, m?: string) => Promise<boolean>
  setMsg: (s: string | null) => void
}) {
  const [showPurge, setShowPurge] = useState(false)
  const [purgeText, setPurgeText] = useState('')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Alert type="info">
        <ShieldCheck size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
        Emotional and personal signals are written to memory <strong>local-only</strong> — they never egress to cloud LLM providers.
      </Alert>

      <Card style={{ padding: '14px 18px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>Strict-private mode</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Restrict coaching inference to self-hosted models only — no cloud provider sees your context.</div>
        </div>
        <Toggle checked={data.strict_private} disabled={working}
          onChange={(v) => action(() => setCoachStrictPrivate(v), v ? 'Strict-private enabled' : 'Strict-private disabled')} />
      </Card>

      <Card style={{ padding: '14px 18px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>Right to be forgotten</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Delete all {data.recent_signals.length} captured signal(s) and forget their linked memory nodes.</div>
        </div>
        <button className="btn btn-danger btn-sm" disabled={working || data.recent_signals.length === 0}
          onClick={() => { setShowPurge(true); setPurgeText('') }}>
          <Trash2 size={13} /> Purge all
        </button>
      </Card>

      <Modal open={showPurge} onClose={() => setShowPurge(false)} title="Purge all signals">
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
          Type <strong style={{ color: 'var(--accent)' }}>PURGE</strong> to permanently delete every captured signal and forget its memory nodes. This cannot be undone.
        </p>
        <input className="form-input" value={purgeText} onChange={e => setPurgeText(e.target.value)} placeholder="PURGE" style={{ marginBottom: 14 }} />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={() => setShowPurge(false)}>Cancel</button>
          <button className="btn btn-danger" disabled={purgeText !== 'PURGE' || working}
            onClick={() => action(async () => {
              const r = await purgeCoach(purgeText)
              if (!r.ok) throw new Error(r.message || 'Purge failed')
              setMsg(`Purged ${r.deleted_signals ?? 0} signal(s), forgot ${r.forgotten_memories ?? 0} memory node(s)`)
            }).then(ok => { if (ok) setShowPurge(false) })}>
            Purge
          </button>
        </div>
      </Modal>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Activity
// --------------------------------------------------------------------------- //

function Activity({ data }: { data: CoachData }) {
  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>Audit log</div>
      {data.audit.length === 0
        ? <Empty message="No audit entries yet." />
        : (
          <div className="glass-card" style={{ overflow: 'hidden' }}>
            <table className="data-table">
              <thead><tr><th>Action</th><th>Status</th><th>Source</th><th>Detail</th><th>When</th></tr></thead>
              <tbody>
                {data.audit.map(a => (
                  <tr key={a.id}>
                    <td style={{ fontSize: 12, fontFamily: 'monospace' }}>{a.action}</td>
                    <td><span className={`badge ${a.status === 'ok' ? 'badge-green' : a.status === 'error' || a.status === 'failed' ? 'badge-red' : 'badge-yellow'}`}>{a.status}</span></td>
                    <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{a.source || '—'}</td>
                    <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{a.detail || '—'}</td>
                    <td style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{fmtDate(a.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </div>
  )
}
