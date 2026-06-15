import { useState, useEffect } from 'react'
import { getMyProfile, updateMyEmail, changeMyPassword } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/contexts/AuthContext'
import { Spinner, Alert, FormField, PageHeader } from '@/components/ui'
import { GlassCard } from '@/components/glass'
import { fmtDate } from '@/lib/utils'
import { Mail, KeyRound, ShieldCheck, User as UserIcon, Save } from 'lucide-react'

export function Profile() {
  const { user: me, refetch } = useAuth()
  const { data, loading, error, refetch: reloadProfile } = useApi(getMyProfile, [])
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  // Email form
  const [email, setEmail] = useState('')
  const [savingEmail, setSavingEmail] = useState(false)

  // Password form
  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [savingPw, setSavingPw] = useState(false)

  useEffect(() => {
    if (data?.user) setEmail(data.user.email || '')
  }, [data])

  function flash(setter: (v: string | null) => void, value: string) {
    setter(value)
    setTimeout(() => setter(null), 4000)
  }

  const isDevBypass = me?.dev_bypass

  async function handleSaveEmail() {
    setErr(null)
    setSavingEmail(true)
    try {
      await updateMyEmail(email.trim())
      flash(setMsg, 'Email updated')
      reloadProfile()
      refetch()
    } catch (e: unknown) {
      flash(setErr, e instanceof Error ? e.message : 'Error')
    } finally {
      setSavingEmail(false)
    }
  }

  async function handleChangePassword() {
    setErr(null)
    if (newPw.length < 8) {
      flash(setErr, 'New password must be at least 8 characters.')
      return
    }
    if (newPw !== confirmPw) {
      flash(setErr, 'New password and confirmation do not match.')
      return
    }
    setSavingPw(true)
    try {
      await changeMyPassword(currentPw, newPw)
      setCurrentPw(''); setNewPw(''); setConfirmPw('')
      flash(setMsg, 'Password changed')
    } catch (e: unknown) {
      flash(setErr, e instanceof Error ? e.message : 'Error')
    } finally {
      setSavingPw(false)
    }
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="My Profile"
        subtitle="Manage your own account — email and password"
      />

      {msg && <Alert type="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && !isDevBypass && <Alert type="error">{error}</Alert>}

      {isDevBypass && (
        <Alert type="info">
          You are signed in with the developer bypass, which has no real account.
          Sign in as a regular user to manage a profile.
        </Alert>
      )}

      {data && !isDevBypass && (
        <div style={{ display: 'grid', gap: 16, maxWidth: 560 }}>
          {/* Account summary */}
          <GlassCard padding={20}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
              <div style={{
                width: 40, height: 40, borderRadius: 10, flexShrink: 0,
                background: 'var(--bg-glass-active)',
                border: '1px solid var(--glass-border-strong)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <UserIcon size={18} style={{ opacity: 0.8 }} />
              </div>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>
                  {data.user.username}
                  {data.user.is_admin && (
                    <span className="badge badge-blue" style={{ marginLeft: 8 }}>
                      <ShieldCheck size={11} style={{ marginRight: 4 }} />Admin
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  Joined {data.user.created_at ? fmtDate(data.user.created_at) : '—'}
                  {data.user.last_login_at && ` · Last login ${fmtDate(data.user.last_login_at)}`}
                </div>
              </div>
            </div>
          </GlassCard>

          {/* Email */}
          <GlassCard padding={20}>
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 14px', display: 'flex', alignItems: 'center', gap: 7 }}>
              <Mail size={15} style={{ opacity: 0.7 }} /> Email address
            </h3>
            <FormField label="Email" hint="optional">
              <input
                className="form-input"
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com"
              />
            </FormField>
            <button
              className="btn btn-primary"
              onClick={handleSaveEmail}
              disabled={savingEmail || email.trim() === (data.user.email || '')}
            >
              <Save size={15} /> {savingEmail ? 'Saving…' : 'Save email'}
            </button>
          </GlassCard>

          {/* Password */}
          <GlassCard padding={20}>
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 14px', display: 'flex', alignItems: 'center', gap: 7 }}>
              <KeyRound size={15} style={{ opacity: 0.7 }} /> Change password
            </h3>
            <FormField label="Current password">
              <input
                className="form-input"
                type="password"
                value={currentPw}
                onChange={e => setCurrentPw(e.target.value)}
                autoComplete="current-password"
              />
            </FormField>
            <FormField label="New password" hint="at least 8 characters">
              <input
                className="form-input"
                type="password"
                value={newPw}
                onChange={e => setNewPw(e.target.value)}
                autoComplete="new-password"
              />
            </FormField>
            <FormField label="Confirm new password">
              <input
                className="form-input"
                type="password"
                value={confirmPw}
                onChange={e => setConfirmPw(e.target.value)}
                autoComplete="new-password"
              />
            </FormField>
            <button
              className="btn btn-primary"
              onClick={handleChangePassword}
              disabled={savingPw || !currentPw || !newPw || !confirmPw}
            >
              <KeyRound size={15} /> {savingPw ? 'Updating…' : 'Update password'}
            </button>
          </GlassCard>
        </div>
      )}
    </div>
  )
}
