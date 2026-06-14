import { useState, useRef, useEffect } from 'react'
import {
  getAvatar, getAvatarVoices, previewAvatarVoice, createAvatarProfile,
  updateAvatarProfile, deleteAvatarProfile, startAvatarSession, recordAvatarConsent,
  endAvatarSession, retryAvatarSession,
  AvatarData, AvatarProfile, AvatarSession, AvatarVoiceStatus, AvatarBackend,
  AvatarSessionStartResult,
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, Tabs, Modal, PageHeader, Empty, FormField, Toggle } from '@/components/ui'
import { AvatarCall, VoiceOnlyCall } from '@/components/AvatarCall'
import { fmtDate } from '@/lib/utils'
import {
  UserSquare, ShieldAlert, Plus, Trash2, Pencil, Play, Square,
  Video, Mic, AlertTriangle, CheckCircle2, XCircle, RefreshCw,
} from 'lucide-react'

const SESSION_STATUS_COLOR: Record<string, string> = {
  active: 'badge-green', pending_consent: 'badge-yellow', needs_provider: 'badge-yellow',
  denied: 'badge-gray', ended: 'badge-gray', blocked: 'badge-red',
}

export function Avatar() {
  const [tab, setTab] = useState('Overview')
  const { data, loading, error, refetch } = useApi(getAvatar, [])
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
        title="Avatar"
        subtitle="Persona + voice + real-time avatar identity for your bots — AI-use disclosure and recording consent enforced on every call"
        actions={<button className="btn btn-ghost btn-sm" disabled={working} onClick={() => refetch()}><RefreshCw size={14} /> Refresh</button>}
      />
      {msg && <Alert type="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}

      <Tabs tabs={['Overview', 'Profiles', 'Voice', 'Sessions', 'Activity']} active={tab} onChange={setTab} />

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && tab === 'Overview' && <Overview data={data} />}
      {data && tab === 'Profiles' && <Profiles data={data} working={working} action={action} setMsg={setMsg} setErr={setErr} />}
      {data && tab === 'Voice' && <VoiceTab data={data} setErr={setErr} />}
      {data && tab === 'Sessions' && <Sessions data={data} working={working} action={action} setMsg={setMsg} setErr={setErr} />}
      {data && tab === 'Activity' && <Activity data={data} />}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Overview
// --------------------------------------------------------------------------- //

function VoiceStatusCard({ s }: { s: AvatarVoiceStatus }) {
  return (
    <Card style={{ padding: '14px 18px', flex: 1, minWidth: 240 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Mic size={15} style={{ opacity: 0.7 }} />
        <span style={{ fontSize: 14, fontWeight: 700, textTransform: 'capitalize' }}>{s.provider} (voice)</span>
        <span className={`badge ${s.connected ? 'badge-green' : 'badge-gray'}`}>
          {s.connected ? 'Connected' : 'Not connected'}
        </span>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>{s.detail}</p>
      {s.source && <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>Source: {s.source}</p>}
    </Card>
  )
}

function BackendCard({ name, b }: { name: string; b: AvatarBackend }) {
  return (
    <Card style={{ padding: '14px 18px', flex: 1, minWidth: 240 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Video size={15} style={{ opacity: 0.7 }} />
        <span style={{ fontSize: 14, fontWeight: 700, textTransform: 'capitalize' }}>{name} (avatar)</span>
        <span className={`badge ${b.configured ? 'badge-green' : 'badge-gray'}`}>
          {b.configured ? 'Configured' : 'Not configured'}
        </span>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>{b.detail}</p>
      {b.source && <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>Source: {b.source}</p>}
    </Card>
  )
}

function Overview({ data }: { data: AvatarData }) {
  const backends = Object.entries(data.avatar.providers)
  const activeProfiles = data.profiles.filter(p => p.active).length
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Alert type="info">
        <ShieldAlert size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
        <strong>Disclosure (shown on every call):</strong> {data.disclosure}
      </Alert>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <VoiceStatusCard s={data.voice} />
        {backends.map(([name, b]) => <BackendCard key={name} name={name} b={b} />)}
      </div>

      <Card style={{ padding: '14px 18px' }}>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>
          Voice synthesis (ElevenLabs) runs here. Realistic real-time avatar <strong>video</strong> runs
          on the external GPU stack (HeyGen Streaming or an Azure-hosted bridge) — this app only
          negotiates the session over HTTPS and the browser connects directly via WebRTC. When no
          avatar backend is configured, sessions degrade honestly to <strong>voice-only</strong>.
        </div>
      </Card>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Card style={{ padding: '14px 18px', flex: 1, minWidth: 160 }}>
          <div style={{ fontSize: 24, fontWeight: 800 }}>{data.profiles.length}</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Profiles ({activeProfiles} active)</div>
        </Card>
        <Card style={{ padding: '14px 18px', flex: 1, minWidth: 160 }}>
          <div style={{ fontSize: 24, fontWeight: 800 }}>{data.sessions.length}</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Recent sessions</div>
        </Card>
        <Card style={{ padding: '14px 18px', flex: 1, minWidth: 160 }}>
          <div style={{ fontSize: 24, fontWeight: 800, color: data.avatar.configured ? '#3fb950' : undefined }}>
            {data.avatar.configured ? 'Video' : 'Voice'}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Default mode</div>
        </Card>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Profiles
// --------------------------------------------------------------------------- //

function Profiles({ data, working, action, setMsg, setErr }: {
  data: AvatarData; working: boolean
  action: (fn: () => Promise<unknown>, m?: string) => Promise<boolean>
  setMsg: (s: string | null) => void; setErr: (s: string | null) => void
}) {
  const [edit, setEdit] = useState<AvatarProfile | null>(null)
  const [showCreate, setShowCreate] = useState(false)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button className="btn btn-secondary btn-sm" onClick={() => setShowCreate(true)}>
          <Plus size={13} /> New profile
        </button>
      </div>

      {data.profiles.length === 0
        ? <Empty message="No avatar profiles yet. Create one to give a bot a persona, voice and avatar." icon={<UserSquare size={32} />} />
        : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {data.profiles.map(p => (
              <Card key={p.id} style={{ padding: '14px 18px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                      <span style={{ fontSize: 14, fontWeight: 700 }}>{p.name}</span>
                      <span className={`badge ${p.active ? 'badge-green' : 'badge-gray'}`}>{p.active ? 'active' : 'inactive'}</span>
                      {p.bot_id !== null && <span className="badge badge-gray">bot #{p.bot_id}</span>}
                    </div>
                    {p.persona && <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>{p.persona}</p>}
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', fontSize: 11, color: 'var(--text-muted)' }}>
                      <span className="badge badge-gray"><Mic size={10} style={{ verticalAlign: -1 }} /> {p.voice_provider || 'no voice'}{p.voice_id ? ` · ${p.voice_id.slice(0, 10)}` : ''}</span>
                      <span className="badge badge-gray"><Video size={10} style={{ verticalAlign: -1 }} /> {p.avatar_provider || 'voice-only'}</span>
                      {p.disclosure_required && <span className="badge badge-gray">disclosure</span>}
                      {p.consent_required && <span className="badge badge-gray">consent</span>}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button className="btn btn-ghost btn-sm" disabled={working} onClick={() => setEdit(p)}><Pencil size={12} /></button>
                    <button className="btn btn-ghost btn-sm" disabled={working}
                      onClick={() => { if (confirm(`Delete avatar profile "${p.name}"?`)) action(() => deleteAvatarProfile(p.id), 'Profile deleted') }}>
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}

      {showCreate && (
        <ProfileModal title="New avatar profile" onClose={() => setShowCreate(false)}
          onSave={async (payload) => {
            const ok = await action(() => createAvatarProfile(payload), 'Profile created')
            if (ok) setShowCreate(false)
            return ok
          }} setErr={setErr} />
      )}
      {edit && (
        <ProfileModal title={`Edit "${edit.name}"`} profile={edit} onClose={() => setEdit(null)}
          onSave={async (payload) => {
            const ok = await action(() => updateAvatarProfile(edit.id, payload), 'Profile updated')
            if (ok) setEdit(null)
            return ok
          }} setErr={setErr} setMsg={setMsg} />
      )}
    </div>
  )
}

const AVATAR_PROVIDERS = ['', 'heygen', 'azure', 'nvidia-ace', 'custom', 'audio2face']

function ProfileModal({ title, profile, onClose, onSave, setErr }: {
  title: string; profile?: AvatarProfile; onClose: () => void
  onSave: (payload: Record<string, string | boolean>) => Promise<boolean>
  setErr: (s: string | null) => void; setMsg?: (s: string | null) => void
}) {
  const [form, setForm] = useState({
    name: profile?.name || '',
    persona: profile?.persona || '',
    voice_provider: profile?.voice_provider || 'elevenlabs',
    voice_id: profile?.voice_id || '',
    avatar_provider: profile?.avatar_provider || '',
    avatar_config: profile?.avatar_config ? JSON.stringify(profile.avatar_config, null, 2) : '',
    disclosure_required: profile?.disclosure_required ?? true,
    consent_required: profile?.consent_required ?? true,
    bot_id: profile?.bot_id != null ? String(profile.bot_id) : '',
  })
  const [saving, setSaving] = useState(false)
  function set<K extends keyof typeof form>(k: K, v: typeof form[K]) { setForm(f => ({ ...f, [k]: v })) }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!form.name.trim()) { setErr('A profile name is required.'); return }
    if (form.avatar_config.trim()) {
      try { JSON.parse(form.avatar_config) } catch { setErr('Avatar config must be valid JSON.'); return }
    }
    setSaving(true)
    const payload: Record<string, string | boolean> = {
      name: form.name.trim(),
      persona: form.persona.trim(),
      voice_provider: form.voice_provider.trim(),
      voice_id: form.voice_id.trim(),
      avatar_provider: form.avatar_provider.trim(),
      avatar_config: form.avatar_config.trim(),
      disclosure_required: form.disclosure_required,
      consent_required: form.consent_required,
      bot_id: form.bot_id.trim(),
    }
    await onSave(payload)
    setSaving(false)
  }

  return (
    <Modal open onClose={onClose} title={title}>
      <form onSubmit={submit}>
        <FormField label="Name *"><input className="form-input" value={form.name} onChange={e => set('name', e.target.value)} required /></FormField>
        <FormField label="Persona" hint="Short description of tone & personality (used by the avatar/voice)">
          <textarea className="form-input" rows={2} value={form.persona} onChange={e => set('persona', e.target.value)} placeholder="e.g. warm, concise, professional sales rep" />
        </FormField>
        <div style={{ display: 'flex', gap: 10 }}>
          <FormField label="Voice provider"><input className="form-input" value={form.voice_provider} onChange={e => set('voice_provider', e.target.value)} placeholder="elevenlabs" /></FormField>
          <FormField label="Voice ID" hint="From the Voice tab"><input className="form-input" value={form.voice_id} onChange={e => set('voice_id', e.target.value)} placeholder="optional" /></FormField>
        </div>
        <FormField label="Avatar provider" hint="Leave empty for voice-only">
          <select className="form-input" value={form.avatar_provider} onChange={e => set('avatar_provider', e.target.value)}>
            {AVATAR_PROVIDERS.map(p => <option key={p} value={p}>{p || '(voice-only)'}</option>)}
          </select>
        </FormField>
        <FormField label="Avatar config (JSON)" hint='Provider-specific, e.g. {"avatar_id": "...", "quality": "high"}'>
          <textarea className="form-input" rows={3} value={form.avatar_config} onChange={e => set('avatar_config', e.target.value)} placeholder="optional JSON" style={{ fontFamily: 'monospace', fontSize: 12 }} />
        </FormField>
        <FormField label="Link to bot ID"><input className="form-input" value={form.bot_id} onChange={e => set('bot_id', e.target.value)} placeholder="optional" /></FormField>
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', margin: '4px 0 14px' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
            <Toggle checked={form.disclosure_required} onChange={v => set('disclosure_required', v)} /> Require AI-use disclosure
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
            <Toggle checked={form.consent_required} onChange={v => set('consent_required', v)} /> Require recording consent
          </label>
        </div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Voice (list + preview)
// --------------------------------------------------------------------------- //

function VoiceTab({ data, setErr }: { data: AvatarData; setErr: (s: string | null) => void }) {
  const { data: voiceData, loading, error } = useApi(getAvatarVoices, [], data.voice.connected)
  const [text, setText] = useState('Hi, this is a quick voice preview from your avatar.')
  const [voiceId, setVoiceId] = useState('')
  const [busy, setBusy] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const urlRef = useRef<string | null>(null)

  useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current) }, [])

  if (!data.voice.connected) {
    return <Alert type="info">Voice is not connected. Add an <strong>ELEVENLABS_API_KEY</strong> in the API Vault (or connect ElevenLabs) to list voices and synthesize previews.</Alert>
  }

  async function preview(e: React.FormEvent) {
    e.preventDefault()
    const vid = voiceId.trim()
    if (!text.trim() || !vid) { setErr('Pick a voice and enter preview text.'); return }
    setBusy(true); setErr(null)
    try {
      const url = await previewAvatarVoice(text.trim(), vid)
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      urlRef.current = url
      if (audioRef.current) { audioRef.current.src = url; await audioRef.current.play() }
    } catch (err: unknown) { setErr(err instanceof Error ? err.message : 'Error') }
    finally { setBusy(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Card style={{ padding: '14px 18px' }}>
        <form onSubmit={preview}>
          <FormField label="Voice">
            <select className="form-input" value={voiceId} onChange={e => setVoiceId(e.target.value)}>
              <option value="">Select a voice…</option>
              {(voiceData?.voices || []).map(v => (
                <option key={v.voice_id} value={v.voice_id}>{v.name || v.voice_id}{v.category ? ` (${v.category})` : ''}</option>
              ))}
            </select>
          </FormField>
          <FormField label="Preview text" hint="Max 500 characters">
            <textarea className="form-input" rows={2} value={text} maxLength={500} onChange={e => setText(e.target.value)} />
          </FormField>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
            <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !voiceId}>
              <Play size={13} /> {busy ? 'Synthesizing…' : 'Preview voice'}
            </button>
          </div>
        </form>
        <audio ref={audioRef} controls style={{ width: '100%', marginTop: 12 }} />
      </Card>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 20 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}
      {voiceData && (voiceData.voices.length === 0
        ? <Empty message="No voices returned by the provider." icon={<Mic size={32} />} />
        : (
          <div className="glass-card" style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead><tr><th>Voice</th><th>Category</th><th>Voice ID</th></tr></thead>
              <tbody>
                {voiceData.voices.map(v => (
                  <tr key={v.voice_id} style={{ cursor: 'pointer' }} onClick={() => setVoiceId(v.voice_id)}>
                    <td style={{ fontSize: 13 }}>{v.name || '—'}</td>
                    <td><span className="badge badge-gray">{v.category || '—'}</span></td>
                    <td style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-muted)' }}>{v.voice_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Sessions (consent-gated lifecycle)
// --------------------------------------------------------------------------- //

function Sessions({ data, working, action, setMsg, setErr }: {
  data: AvatarData; working: boolean
  action: (fn: () => Promise<unknown>, m?: string) => Promise<boolean>
  setMsg: (s: string | null) => void; setErr: (s: string | null) => void
}) {
  const [profileId, setProfileId] = useState('')
  const [consent, setConsent] = useState<AvatarSessionStartResult | null>(null)
  const [starting, setStarting] = useState(false)
  const [activeCall, setActiveCall] = useState<AvatarSession | null>(null)
  const [ending, setEnding] = useState(false)

  const activeProfiles = data.profiles.filter(p => p.active)
  const callProfile = activeCall
    ? data.profiles.find(p => p.id === activeCall.avatar_profile_id) : undefined

  function openIfActive(session: AvatarSession) {
    if (session.status === 'active') setActiveCall(session)
  }

  async function endCall() {
    if (!activeCall) return
    const sid = activeCall.id
    setActiveCall(null) // unmount the call panel → local media torn down immediately
    setEnding(true)
    await action(() => endAvatarSession(sid), `Session #${sid} ended`)
    setEnding(false)
  }

  async function endSessionRow(sid: number) {
    // Ending a row from the table must also tear down the call panel if that
    // same session is the one currently open — otherwise its live media keeps
    // running after the backend session has ended.
    if (activeCall && activeCall.id === sid) setActiveCall(null)
    await action(() => endAvatarSession(sid), `Session #${sid} ended`)
  }

  async function retryRow(sid: number) {
    // Re-attempt activation in place for a needs_provider session whose consent
    // was already granted — the callee is never re-prompted for consent. If it
    // recovers to active, open the live call panel straight away.
    let started: AvatarSession | null = null
    const ok = await action(async () => {
      const r = await retryAvatarSession(sid)
      setMsg(`Session #${sid} ${r.session.status} (${r.session.mode || 'voice_only'}).`)
      started = r.session
    })
    if (ok && started) openIfActive(started)
  }

  async function start() {
    if (!profileId) { setErr('Select a profile to start a session.'); return }
    setStarting(true); setErr(null); setMsg(null)
    try {
      const res = await startAvatarSession(Number(profileId))
      if (res.session.status === 'pending_consent') {
        setConsent(res)
      } else {
        setMsg(`Session #${res.session.id} ${res.session.status} (${res.session.mode || 'voice_only'}).`)
        openIfActive(res.session)
      }
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setStarting(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {activeCall && (
        activeCall.mode === 'avatar_video'
          ? <AvatarCall session={activeCall} onEnd={endCall} ending={ending} />
          : <VoiceOnlyCall session={activeCall} profile={callProfile} onEnd={endCall} ending={ending} />
      )}
      <Card style={{ padding: '14px 18px' }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Start a session</div>
        {activeProfiles.length === 0
          ? <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>Create an active profile first (Profiles tab).</p>
          : (
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div style={{ flex: 1, minWidth: 220 }}>
                <FormField label="Profile">
                  <select className="form-input" value={profileId} onChange={e => setProfileId(e.target.value)}>
                    <option value="">Select a profile…</option>
                    {activeProfiles.map(p => (
                      <option key={p.id} value={p.id}>{p.name} — {p.avatar_provider || 'voice-only'}</option>
                    ))}
                  </select>
                </FormField>
              </div>
              <button className="btn btn-primary" disabled={starting || !profileId} onClick={start} style={{ marginBottom: 14 }}>
                <Play size={14} /> {starting ? 'Starting…' : 'Start session'}
              </button>
            </div>
          )}
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          The callee sees the AI-use disclosure and must grant recording consent before a session goes live.
        </p>
      </Card>

      {data.sessions.length === 0
        ? <Empty message="No sessions yet." icon={<Video size={32} />} />
        : (
          <div className="glass-card" style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead><tr><th>When</th><th>Profile</th><th>Mode</th><th>Status</th><th>Consent</th><th>Detail</th><th></th></tr></thead>
              <tbody>
                {data.sessions.map(s => {
                  const prof = data.profiles.find(p => p.id === s.avatar_profile_id)
                  const endable = !['ended', 'denied'].includes(s.status)
                  return (
                    <tr key={s.id}>
                      <td style={{ fontSize: 11, whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>{fmtDate(s.created_at)}</td>
                      <td style={{ fontSize: 12 }}>{prof?.name || `#${s.avatar_profile_id}`}</td>
                      <td>{s.mode ? <span className="badge badge-gray">{s.mode}</span> : '—'}</td>
                      <td><span className={`badge ${SESSION_STATUS_COLOR[s.status] || 'badge-gray'}`}>{s.status}</span></td>
                      <td style={{ fontSize: 12 }}>{s.consent_status}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)', maxWidth: 260 }}>{s.result_detail || '—'}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        {s.status === 'needs_provider' && s.consent_status === 'granted' && (
                          <button className="btn btn-ghost btn-sm" disabled={working}
                            onClick={() => retryRow(s.id)} title="Re-attempt activation without re-collecting consent">
                            <RefreshCw size={12} /> Retry
                          </button>
                        )}
                        {endable && (
                          <button className="btn btn-ghost btn-sm" disabled={working}
                            onClick={() => endSessionRow(s.id)}>
                            <Square size={12} /> End
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

      {consent && (
        <ConsentModal start={consent} onClose={() => setConsent(null)}
          onDecision={async (granted) => {
            const sid = consent.session.id
            let started: AvatarSession | null = null
            const ok = await action(async () => {
              const r = await recordAvatarConsent(sid, granted)
              if (granted) {
                setMsg(`Session #${sid} ${r.session.status} (${r.session.mode || 'voice_only'}).`)
                started = r.session
              } else setMsg(`Session #${sid} consent denied.`)
            })
            if (ok) {
              setConsent(null)
              if (started) openIfActive(started)
            }
          }} working={working} />
      )}
    </div>
  )
}

function ConsentModal({ start, onClose, onDecision, working }: {
  start: AvatarSessionStartResult; onClose: () => void
  onDecision: (granted: boolean) => void; working: boolean
}) {
  return (
    <Modal open onClose={onClose} title="AI-use disclosure & recording consent">
      <Alert type="info">
        <ShieldAlert size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
        {start.disclosure}
      </Alert>
      <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '12px 0' }}>{start.consent_prompt}</p>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
        <button className="btn btn-secondary" disabled={working} onClick={() => onDecision(false)}>
          <XCircle size={14} /> Decline
        </button>
        <button className="btn btn-primary" disabled={working} onClick={() => onDecision(true)}>
          <CheckCircle2 size={14} /> Consent & continue
        </button>
      </div>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Activity (audit)
// --------------------------------------------------------------------------- //

function Activity({ data }: { data: AvatarData }) {
  if (data.audit.length === 0) return <Empty message="No avatar activity yet." icon={<AlertTriangle size={32} />} />
  return (
    <div className="glass-card" style={{ overflowX: 'auto' }}>
      <table className="data-table">
        <thead><tr><th>When</th><th>Action</th><th>Status</th><th>Detail</th></tr></thead>
        <tbody>
          {data.audit.map((a, i) => (
            <tr key={i}>
              <td style={{ fontSize: 11, whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>{fmtDate(a.created_at)}</td>
              <td style={{ fontSize: 12, fontFamily: 'monospace' }}>{a.action}</td>
              <td><span className={`badge ${a.status === 'ok' ? 'badge-green' : a.status === 'blocked' ? 'badge-red' : 'badge-yellow'}`}>{a.status}</span></td>
              <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{a.detail || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
