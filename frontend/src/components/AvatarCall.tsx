import { useEffect, useRef, useState } from 'react'
import type { Room, RemoteTrack } from 'livekit-client'
import { AvatarSession, AvatarProfile, startAvatarStream, previewAvatarVoice } from '@/lib/api'
import { Card, Alert } from '@/components/ui'
import { Video, Mic, Square, Play, Loader2, AlertTriangle, ShieldAlert } from 'lucide-react'

// ---------------------------------------------------------------------------
// Live in-browser avatar call.
//
// The backend negotiates the streaming session over HTTPS and returns a
// connection descriptor; the browser connects DIRECTLY to the provider's
// real-time stack with it. Two concrete, standard transports are supported:
//
//   • LiveKit  — descriptor { url: "wss://…", access_token | token }
//                (HeyGen returns this shape; LiveKit-backed bridges too).
//   • WHEP     — descriptor { whep_url: "https://…", ice_servers?, token? }
//                (standard WebRTC-HTTP egress; provider-agnostic).
//
// Anything else fails loud with an honest "unsupported descriptor" panel — we
// never fake a stream (governance pillar #1). The descriptor can carry session
// tokens, so it lives only in React state and is NEVER logged or persisted.
// ---------------------------------------------------------------------------

type ConnState = 'connecting' | 'connected' | 'error' | 'unsupported' | 'ended'

const STATE_LABEL: Record<ConnState, string> = {
  connecting: 'Connecting…', connected: 'Live', error: 'Connection error',
  unsupported: 'Unsupported', ended: 'Ended',
}
const STATE_BADGE: Record<ConnState, string> = {
  connecting: 'badge-yellow', connected: 'badge-green', error: 'badge-red',
  unsupported: 'badge-red', ended: 'badge-gray',
}

function pick(obj: Record<string, unknown> | null | undefined, ...keys: string[]): string | null {
  if (!obj) return null
  for (const k of keys) {
    const v = obj[k]
    if (typeof v === 'string' && v.trim()) return v.trim()
  }
  return null
}

function isSecureWs(url: string): boolean {
  return url.startsWith('wss://') || /^wss?:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(url)
}

function iceGatheringComplete(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === 'complete') return Promise.resolve()
  return new Promise(resolve => {
    const done = () => { pc.removeEventListener('icegatheringstatechange', check); resolve() }
    const check = () => { if (pc.iceGatheringState === 'complete') done() }
    pc.addEventListener('icegatheringstatechange', check)
    setTimeout(done, 3000) // fail-safe: send the candidates gathered so far
  })
}

export function AvatarCall({ session, onEnd, ending }: {
  session: AvatarSession; onEnd: () => void; ending: boolean
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const audioRef = useRef<HTMLAudioElement>(null)
  const roomRef = useRef<Room | null>(null)
  const pcRef = useRef<RTCPeerConnection | null>(null)

  const [state, setState] = useState<ConnState>('connecting')
  const [detail, setDetail] = useState<string>('')

  const conn = (session.connection || null) as Record<string, unknown> | null
  const provider = (session.provider || '').toLowerCase()
  const lkUrl = pick(conn, 'url')
  const lkToken = pick(conn, 'access_token', 'token')
  const whepUrl = pick(conn, 'whep_url', 'whep')

  useEffect(() => {
    let cancelled = false

    function attach(kind: 'video' | 'audio', stream: MediaStream) {
      const el = kind === 'video' ? videoRef.current : audioRef.current
      if (el) el.srcObject = stream
    }

    async function connectLiveKit(url: string, token: string) {
      if (!isSecureWs(url)) {
        setState('error')
        setDetail('Refusing to open an insecure (non-wss) avatar connection.')
        return
      }
      const livekit = await import('livekit-client')
      if (cancelled) return
      const room = new livekit.Room({ adaptiveStream: true, dynacast: true })
      roomRef.current = room
      room.on(livekit.RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
        if (track.kind === livekit.Track.Kind.Video && videoRef.current) {
          track.attach(videoRef.current)
        } else if (track.kind === livekit.Track.Kind.Audio && audioRef.current) {
          track.attach(audioRef.current)
        }
      })
      room.on(livekit.RoomEvent.Disconnected, () => {
        if (!cancelled) setState(s => (s === 'connected' ? 'ended' : s))
      })
      await room.connect(url, token)
      if (cancelled) { room.disconnect(); return }
      setState('connected')
      setDetail('Connected to the live avatar stream.')
      // HeyGen only publishes the avatar tracks after streaming.start (which
      // needs the server-side key) — trigger it now that the room is joined.
      if (provider === 'heygen') {
        try {
          await startAvatarStream(session.id)
        } catch (e: unknown) {
          // Without streaming.start the avatar publishes no tracks, so the room
          // is joined but no video/audio will ever flow — surface that honestly
          // as an error rather than leaving a misleading "Live" badge.
          if (!cancelled) {
            setState('error')
            setDetail(e instanceof Error
              ? `The avatar stream could not be started: ${e.message}`
              : 'The avatar stream could not be started.')
          }
        }
      }
    }

    async function connectWhep(url: string) {
      if (!url.startsWith('https://')) {
        setState('error')
        setDetail('Refusing to open an insecure (non-https) WHEP connection.')
        return
      }
      const iceServers = Array.isArray(conn?.ice_servers)
        ? (conn!.ice_servers as RTCIceServer[]) : []
      const token = pick(conn, 'token', 'access_token')
      const pc = new RTCPeerConnection({ iceServers })
      pcRef.current = pc
      pc.ontrack = (e) => {
        const [stream] = e.streams
        if (stream) attach(e.track.kind === 'audio' ? 'audio' : 'video', stream)
      }
      pc.onconnectionstatechange = () => {
        if (cancelled) return
        if (pc.connectionState === 'connected') setState('connected')
        else if (pc.connectionState === 'failed') {
          setState('error'); setDetail('WebRTC peer connection failed.')
        } else if (['disconnected', 'closed'].includes(pc.connectionState)) {
          setState(s => (s === 'connected' ? 'ended' : s))
        }
      }
      pc.addTransceiver('video', { direction: 'recvonly' })
      pc.addTransceiver('audio', { direction: 'recvonly' })
      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      await iceGatheringComplete(pc)
      if (cancelled) return
      const res = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/sdp',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: pc.localDescription?.sdp || offer.sdp || '',
      })
      if (!res.ok) throw new Error(`WHEP endpoint returned HTTP ${res.status}`)
      const answerSdp = await res.text()
      if (cancelled) return
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp })
      setDetail('WebRTC handshake complete — waiting for media…')
    }

    async function run() {
      try {
        if (lkUrl && lkToken) {
          await connectLiveKit(lkUrl, lkToken)
        } else if (whepUrl) {
          await connectWhep(whepUrl)
        } else {
          setState('unsupported')
        }
      } catch (e: unknown) {
        if (!cancelled) {
          setState('error')
          setDetail(e instanceof Error ? e.message : 'Failed to open the avatar stream.')
        }
      }
    }
    run()

    return () => {
      cancelled = true
      try { roomRef.current?.disconnect() } catch { /* ignore */ }
      roomRef.current = null
      const pc = pcRef.current
      if (pc) {
        try { pc.getReceivers().forEach(r => r.track?.stop()) } catch { /* ignore */ }
        try { pc.close() } catch { /* ignore */ }
      }
      pcRef.current = null
      for (const el of [videoRef.current, audioRef.current]) {
        if (el) { try { el.srcObject = null } catch { /* ignore */ } }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.id])

  return (
    <Card style={{ padding: '14px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <Video size={16} style={{ opacity: 0.8 }} />
        <span style={{ fontSize: 14, fontWeight: 700 }}>
          Live avatar call · session #{session.id}
        </span>
        <span className="badge badge-gray" style={{ textTransform: 'capitalize' }}>
          {session.provider || 'provider'}
        </span>
        <span className={`badge ${STATE_BADGE[state]}`}>{STATE_LABEL[state]}</span>
        <button className="btn btn-secondary btn-sm" disabled={ending}
          style={{ marginLeft: 'auto' }} onClick={onEnd}>
          <Square size={13} /> {ending ? 'Ending…' : 'End call'}
        </button>
      </div>

      <div style={{
        position: 'relative', width: '100%', aspectRatio: '16 / 9',
        background: '#0d0d18', borderRadius: 10, overflow: 'hidden',
        border: '1px solid var(--border, rgba(255,255,255,0.08))',
      }}>
        <video ref={videoRef} autoPlay playsInline muted={false}
          style={{ width: '100%', height: '100%', objectFit: 'contain', background: '#0d0d18' }} />
        {state !== 'connected' && (
          <div style={{
            position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--text-muted)',
            textAlign: 'center', padding: 20,
          }}>
            {state === 'connecting' && <Loader2 size={26} className="animate-spin" />}
            {(state === 'error' || state === 'unsupported') && <AlertTriangle size={26} />}
            <span style={{ fontSize: 13 }}>{STATE_LABEL[state]}</span>
          </div>
        )}
      </div>
      {/* Remote audio plays through a dedicated element so it survives video state. */}
      <audio ref={audioRef} autoPlay />

      {state === 'unsupported' && (
        <Alert type="error">
          This session returned a connection descriptor we don't support. Expected either
          a LiveKit descriptor (<code>url</code> + <code>access_token</code>) or a WHEP
          descriptor (<code>whep_url</code>). No stream was opened.
        </Alert>
      )}
      {detail && state !== 'unsupported' && (
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 10 }}>{detail}</p>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Voice-only call — honest synthesized-voice experience (no avatar video).
// ---------------------------------------------------------------------------

export function VoiceOnlyCall({ session, profile, onEnd, ending }: {
  session: AvatarSession; profile?: AvatarProfile; onEnd: () => void; ending: boolean
}) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const urlRef = useRef<string | null>(null)
  const [text, setText] = useState('Hi, this is your avatar speaking over a voice-only session.')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const voiceId = profile?.voice_id || ''

  useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current) }, [])

  async function say(e: React.FormEvent) {
    e.preventDefault()
    if (!text.trim() || !voiceId) return
    setBusy(true); setErr(null)
    try {
      const url = await previewAvatarVoice(text.trim(), voiceId)
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      urlRef.current = url
      if (audioRef.current) { audioRef.current.src = url; await audioRef.current.play() }
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setBusy(false) }
  }

  return (
    <Card style={{ padding: '14px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <Mic size={16} style={{ opacity: 0.8 }} />
        <span style={{ fontSize: 14, fontWeight: 700 }}>
          Voice-only call · session #{session.id}
        </span>
        <span className="badge badge-yellow">No video</span>
        <button className="btn btn-secondary btn-sm" disabled={ending}
          style={{ marginLeft: 'auto' }} onClick={onEnd}>
          <Square size={13} /> {ending ? 'Ending…' : 'End call'}
        </button>
      </div>

      <Alert type="info">
        <ShieldAlert size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
        This is a <strong>voice-only</strong> session — synthesized voice with no avatar
        video. Type a line and the avatar speaks it aloud.
      </Alert>

      {!voiceId ? (
        <Alert type="warn">
          This profile has no voice selected. Pick a voice in the Profiles tab to synthesize speech.
        </Alert>
      ) : (
        <form onSubmit={say} style={{ marginTop: 10 }}>
          <textarea className="form-input" rows={2} value={text} maxLength={500}
            onChange={e => setText(e.target.value)} />
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
            <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !text.trim()}>
              <Play size={13} /> {busy ? 'Synthesizing…' : 'Say this'}
            </button>
          </div>
        </form>
      )}
      {err && <Alert type="error">{err}</Alert>}
      <audio ref={audioRef} controls style={{ width: '100%', marginTop: 12 }} />
    </Card>
  )
}
