import { useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { login, ApiError } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'
import { Shield, Eye, EyeOff, AlertCircle } from 'lucide-react'

export function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState<string | null>(null)
  const [loading, setLoading]   = useState(false)
  const [showPw, setShowPw]     = useState(false)
  const navigate = useNavigate()
  const { refetch, branding } = useAuth()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!username || !password) return
    setLoading(true)
    setError(null)
    try {
      await login(username, password)
      await refetch()
      navigate('/')
    } catch (err) {
      if (err instanceof ApiError) setError(err.message)
      else setError('Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 24,
      position: 'relative',
    }}>
      {/* Extra ambient orbs for the login page */}
      <div style={{
        position: 'fixed', top: '20%', left: '15%',
        width: 400, height: 400,
        background: 'radial-gradient(circle, rgba(233,69,96,0.07) 0%, transparent 65%)',
        pointerEvents: 'none', zIndex: 0,
      }} />
      <div style={{
        position: 'fixed', bottom: '20%', right: '15%',
        width: 350, height: 350,
        background: 'radial-gradient(circle, rgba(124,92,191,0.07) 0%, transparent 65%)',
        pointerEvents: 'none', zIndex: 0,
      }} />

      <div style={{ width: '100%', maxWidth: 400, position: 'relative', zIndex: 1, animation: 'fadeInUp 0.35s ease forwards' }}>

        {/* Branding */}
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{
            width: 60, height: 60, borderRadius: 16,
            background: 'linear-gradient(135deg, rgba(233,69,96,0.2) 0%, rgba(124,92,191,0.15) 100%)',
            border: '1px solid rgba(233,69,96,0.3)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '0 auto 18px',
            boxShadow: '0 0 32px rgba(233,69,96,0.15), 0 8px 24px rgba(0,0,0,0.4)',
          }}>
            <Shield size={26} color="var(--accent)" strokeWidth={1.75} />
          </div>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: 'var(--text-primary)', margin: 0, letterSpacing: '-0.4px' }}>
            {branding.name}
          </h1>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 5, letterSpacing: '0.01em' }}>
            {branding.tagline}
          </p>
        </div>

        {/* Glass card */}
        <div style={{
          background: 'rgba(255,255,255,0.035)',
          backdropFilter: 'blur(28px)',
          WebkitBackdropFilter: 'blur(28px)',
          border: '1px solid rgba(255,255,255,0.1)',
          borderRadius: 20,
          padding: '32px 28px',
          boxShadow: '0 20px 60px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.04) inset',
        }}>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 22 }}>
            Sign in to your account
          </h2>

          {error && (
            <div className="alert alert-error" style={{ marginBottom: 18 }}>
              <AlertCircle size={15} style={{ flexShrink: 0, marginTop: 1 }} />
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label">Username</label>
              <input
                type="text"
                className="form-input"
                value={username}
                onChange={e => setUsername(e.target.value)}
                placeholder="Enter your username"
                autoComplete="username"
                autoFocus
                required
              />
            </div>

            <div className="form-group">
              <label className="form-label">Password</label>
              <div style={{ position: 'relative' }}>
                <input
                  type={showPw ? 'text' : 'password'}
                  className="form-input"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  autoComplete="current-password"
                  required
                  style={{ paddingRight: 42 }}
                />
                <button
                  type="button"
                  onClick={() => setShowPw(!showPw)}
                  style={{
                    position: 'absolute', right: 13, top: '50%', transform: 'translateY(-50%)',
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: 'var(--text-muted)', display: 'flex', alignItems: 'center',
                    padding: 2, borderRadius: 4,
                    transition: 'color 0.15s',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-secondary)')}
                  onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
                >
                  {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading}
              style={{ width: '100%', justifyContent: 'center', marginTop: 8, padding: '11px 18px', fontSize: 14 }}
            >
              {loading ? (
                <>
                  <div className="spinner" style={{ width: 16, height: 16 }} />
                  Signing in…
                </>
              ) : 'Sign in'}
            </button>
          </form>
        </div>

        <p style={{ textAlign: 'center', marginTop: 16, fontSize: 11.5, color: 'var(--text-muted)', letterSpacing: '0.02em' }}>
          Session secured with signed cookies
        </p>
      </div>
    </div>
  )
}
