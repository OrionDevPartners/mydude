import { useState } from 'react'
import {
  getUsers, createUser, toggleUser, resetUserPassword, deleteUser, AppUser,
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/contexts/AuthContext'
import { Spinner, Alert, Modal, FormField, PageHeader, Empty } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import {
  Plus, UserPlus, Trash2, ToggleLeft, ToggleRight, KeyRound, ShieldCheck, Users as UsersIcon,
} from 'lucide-react'

export function Users() {
  const { user: me } = useAuth()
  const { data, loading, error, refetch } = useApi(getUsers, [])
  const [showAdd, setShowAdd] = useState(false)
  const [pwTarget, setPwTarget] = useState<AppUser | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AppUser | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  // Add-user form
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  // Reset-password form
  const [newPw, setNewPw] = useState('')

  function flash(setter: (v: string | null) => void, value: string) {
    setter(value)
    setTimeout(() => setter(null), 4000)
  }

  async function handleCreate() {
    setErr(null)
    try {
      await createUser({ username, password, email: email || undefined, is_admin: isAdmin })
      setShowAdd(false)
      setUsername(''); setEmail(''); setPassword(''); setIsAdmin(false)
      flash(setMsg, 'User created')
      refetch()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Error')
    }
  }

  async function handleToggle(u: AppUser) {
    setErr(null)
    try {
      await toggleUser(u.id)
      flash(setMsg, `${u.username} ${u.is_active ? 'deactivated' : 'activated'}`)
      refetch()
    } catch (e: unknown) {
      flash(setErr, e instanceof Error ? e.message : 'Error')
    }
  }

  async function handleResetPw() {
    if (!pwTarget) return
    setErr(null)
    try {
      await resetUserPassword(pwTarget.id, newPw)
      setPwTarget(null); setNewPw('')
      flash(setMsg, 'Password updated')
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Error')
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return
    setErr(null)
    try {
      await deleteUser(deleteTarget.id)
      setDeleteTarget(null)
      flash(setMsg, 'User deleted')
      refetch()
    } catch (e: unknown) {
      flash(setErr, e instanceof Error ? e.message : 'Error')
      setDeleteTarget(null)
    }
  }

  return (
    <div>
      <PageHeader
        title="Users"
        subtitle="Individual operator accounts — each action is attributable and revocable"
        actions={
          <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}>
            <Plus size={14} /> Add user
          </button>
        }
      />

      {msg && <Alert type="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && (
        data.users.length === 0
          ? <Empty message="No users yet." icon={<UsersIcon size={32} />} />
          : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead>
                  <tr><th>Username</th><th>Email</th><th>Role</th><th>Status</th><th>Last login</th><th style={{ textAlign: 'right' }}>Actions</th></tr>
                </thead>
                <tbody>
                  {data.users.map((u) => {
                    const isSelf = me?.username === u.username
                    return (
                      <tr key={u.id}>
                        <td style={{ fontWeight: 600 }}>
                          {u.username}{isSelf && <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}> (you)</span>}
                        </td>
                        <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{u.email || '—'}</td>
                        <td>
                          {u.is_admin
                            ? <span className="badge badge-blue"><ShieldCheck size={11} style={{ marginRight: 4 }} />Admin</span>
                            : <span className="badge badge-gray">User</span>}
                        </td>
                        <td>
                          {u.is_active
                            ? <span className="badge badge-green">Active</span>
                            : <span className="badge badge-red">Disabled</span>}
                        </td>
                        <td style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{u.last_login_at ? fmtDate(u.last_login_at) : 'never'}</td>
                        <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                          <button className="btn btn-ghost btn-sm" title="Reset password" onClick={() => { setPwTarget(u); setNewPw('') }}>
                            <KeyRound size={14} />
                          </button>
                          <button
                            className="btn btn-ghost btn-sm"
                            title={u.is_active ? 'Deactivate' : 'Activate'}
                            disabled={isSelf}
                            onClick={() => handleToggle(u)}
                          >
                            {u.is_active ? <ToggleRight size={14} color="var(--accent)" /> : <ToggleLeft size={14} />}
                          </button>
                          <button
                            className="btn btn-ghost btn-sm"
                            title="Delete"
                            disabled={isSelf}
                            onClick={() => setDeleteTarget(u)}
                          >
                            <Trash2 size={14} color="var(--danger, #e94560)" />
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )
      )}

      {/* Add user modal */}
      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="Add user">
        <FormField label="Username">
          <input className="form-input" value={username} onChange={e => setUsername(e.target.value)} autoFocus />
        </FormField>
        <FormField label="Email" hint="optional">
          <input className="form-input" type="email" value={email} onChange={e => setEmail(e.target.value)} />
        </FormField>
        <FormField label="Password" hint="at least 8 characters">
          <input className="form-input" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" />
        </FormField>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: 16 }}>
          <input type="checkbox" checked={isAdmin} onChange={e => setIsAdmin(e.target.checked)} />
          Grant admin privileges (can manage users)
        </label>
        <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }} onClick={handleCreate}>
          <UserPlus size={15} /> Create user
        </button>
      </Modal>

      {/* Reset password modal */}
      <Modal open={!!pwTarget} onClose={() => setPwTarget(null)} title={`Reset password — ${pwTarget?.username || ''}`}>
        <FormField label="New password" hint="at least 8 characters">
          <input className="form-input" type="password" value={newPw} onChange={e => setNewPw(e.target.value)} autoComplete="new-password" autoFocus />
        </FormField>
        <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }} onClick={handleResetPw}>
          <KeyRound size={15} /> Update password
        </button>
      </Modal>

      {/* Delete confirm modal */}
      <Modal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} title="Delete user">
        <p style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 20 }}>
          Permanently delete <strong>{deleteTarget?.username}</strong>? This cannot be undone.
        </p>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button className="btn btn-ghost" onClick={() => setDeleteTarget(null)}>Cancel</button>
          <button className="btn btn-danger" onClick={handleDelete}>
            <Trash2 size={15} /> Delete
          </button>
        </div>
      </Modal>
    </div>
  )
}
