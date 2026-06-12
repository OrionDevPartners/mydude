import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  getKeys, addKey, rotateKey, toggleKey, deleteKey, ApiKey
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, Modal, FormField, PageHeader, Empty } from '@/components/ui'
import { fmtDate, statusBadge } from '@/lib/utils'
import {
  Plus, Search, Eye, EyeOff, RefreshCw, Trash2, ToggleLeft, ToggleRight,
  AlertTriangle, Key, FileText, Filter
} from 'lucide-react'

export function Keys() {
  const [q, setQ] = useState('')
  const [category, setCategory] = useState('')
  const [revealId, setRevealId] = useState<number | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [rotateTarget, setRotateTarget] = useState<ApiKey | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ApiKey | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const { data, loading, error, refetch } = useApi(
    () => getKeys({ q: q || undefined, category: category || undefined, reveal: revealId ?? undefined }),
    [q, category, revealId]
  )

  async function handleToggle(key: ApiKey) {
    try {
      await toggleKey(key.id)
      setMsg(`Key ${key.is_active ? 'disabled' : 'enabled'}`)
      refetch()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Error')
    }
  }

  async function handleDelete(key: ApiKey) {
    try {
      await deleteKey(key.id)
      setDeleteTarget(null)
      setMsg('Key deleted')
      refetch()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Error')
    }
  }

  return (
    <div>
      <PageHeader
        title="API Vault"
        subtitle="Encrypted credential storage"
        actions={
          <>
            <Link to="/keys/audit" className="btn btn-secondary btn-sm">
              <FileText size={14} /> Audit log
            </Link>
            <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}>
              <Plus size={14} /> Add key
            </button>
          </>
        }
      />

      {msg && <Alert type="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {(err || error) && <Alert type="error" onClose={() => setErr(null)}>{err || error}</Alert>}

      {/* Encryption key persistence warning */}
      {data && !data.encryption_persistent && (
        <Alert type="error">
          <AlertTriangle size={14} /> <strong>ENCRYPTION_KEY is not set.</strong> The
          vault is using a temporary key generated for this session only — every
          restart creates a new key, which makes previously saved credentials
          undecryptable. Set <code>ENCRYPTION_KEY</code> as a persistent deployment
          secret to keep your saved keys across restarts.
        </Alert>
      )}

      {/* Reminders */}
      {data?.reminders?.map((r, i) => (
        <Alert key={i} type={r.level === 'danger' ? 'error' : 'warn'}>
          <AlertTriangle size={14} /> {r.text}
        </Alert>
      ))}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 18, flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
          <Search size={14} style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input
            className="form-input"
            style={{ paddingLeft: 34 }}
            placeholder="Search keys…"
            value={q}
            onChange={e => setQ(e.target.value)}
          />
        </div>
        <select
          className="form-input"
          style={{ width: 180 }}
          value={category}
          onChange={e => setCategory(e.target.value)}
        >
          <option value="">All categories</option>
          {data?.used_categories?.map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}

      {data && (
        data.keys.length === 0 ? (
          <Empty message="No keys found. Add your first API key." icon={<Key size={32} />} />
        ) : (
          <div className="glass-card" style={{ overflow: 'hidden' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Service</th>
                  <th>Category</th>
                  <th>Key</th>
                  <th>Env Var</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.keys.map(key => (
                  <tr key={key.id}>
                    <td>
                      <div>
                        <span style={{ fontWeight: 600, fontSize: 13 }}>{key.name}</span>
                        {key.label && <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 1 }}>{key.label}</p>}
                      </div>
                    </td>
                    <td>
                      <span className="badge badge-blue">{key.category}</span>
                    </td>
                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>
                      {revealId === key.id && key.revealed ? (
                        <span style={{ color: '#34d399' }}>{key.revealed}</span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)' }}>{key.masked_key}</span>
                      )}
                    </td>
                    <td style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-muted)' }}>
                      {key.env_var}
                    </td>
                    <td>
                      <span className={`badge ${key.is_active ? 'badge-green' : 'badge-gray'}`}>
                        {key.is_active ? 'active' : 'disabled'}
                      </span>
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button
                          className="btn btn-ghost btn-sm"
                          title={revealId === key.id ? 'Hide' : 'Reveal'}
                          onClick={() => setRevealId(revealId === key.id ? null : key.id)}
                        >
                          {revealId === key.id ? <EyeOff size={13} /> : <Eye size={13} />}
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          title="Rotate"
                          onClick={() => setRotateTarget(key)}
                        >
                          <RefreshCw size={13} />
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          title={key.is_active ? 'Disable' : 'Enable'}
                          onClick={() => handleToggle(key)}
                        >
                          {key.is_active ? <ToggleRight size={13} style={{ color: '#34d399' }} /> : <ToggleLeft size={13} />}
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          title="Delete"
                          onClick={() => setDeleteTarget(key)}
                        >
                          <Trash2 size={13} style={{ color: '#f87171' }} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {/* Add key modal */}
      <AddKeyModal
        open={showAdd}
        catalog={data?.catalog || []}
        categories={data?.categories || []}
        onClose={() => setShowAdd(false)}
        onSaved={() => { setShowAdd(false); setMsg('Key saved to vault'); refetch() }}
        onError={setErr}
      />

      {/* Rotate modal */}
      <RotateModal
        target={rotateTarget}
        onClose={() => setRotateTarget(null)}
        onSaved={() => { setRotateTarget(null); setMsg('Key rotated'); refetch() }}
        onError={setErr}
      />

      {/* Delete confirm */}
      <Modal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} title="Delete key">
        <p style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 20 }}>
          Delete <strong style={{ color: 'var(--text-primary)' }}>{deleteTarget?.name}</strong>? This cannot be undone.
        </p>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={() => setDeleteTarget(null)}>Cancel</button>
          <button className="btn btn-danger" onClick={() => deleteTarget && handleDelete(deleteTarget)}>Delete</button>
        </div>
      </Modal>
    </div>
  )
}

function AddKeyModal({ open, catalog, categories, onClose, onSaved, onError }: {
  open: boolean; catalog: ApiKey[]; categories: string[];
  onClose: () => void; onSaved: () => void; onError: (e: string) => void
}) {
  const [form, setForm] = useState({ provider: '', label: '', api_key: '', category: '', env_var: '', notes: '', expires_at: '', rotation_days: '' })
  const [loading, setLoading] = useState(false)

  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    try {
      await addKey(form as unknown as Parameters<typeof addKey>[0])
      onSaved()
      setForm({ provider: '', label: '', api_key: '', category: '', env_var: '', notes: '', expires_at: '', rotation_days: '' })
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : 'Error adding key')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Add API key">
      <form onSubmit={submit}>
        <FormField label="Service / Provider">
          <input className="form-input" placeholder="openai" value={form.provider} onChange={e => set('provider', e.target.value)} required />
        </FormField>
        <FormField label="API Key" hint="Stored encrypted in the vault">
          <input className="form-input" type="password" placeholder="sk-…" value={form.api_key} onChange={e => set('api_key', e.target.value)} required />
        </FormField>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <FormField label="Label">
            <input className="form-input" placeholder="Production" value={form.label} onChange={e => set('label', e.target.value)} />
          </FormField>
          <FormField label="Category">
            <select className="form-input" value={form.category} onChange={e => set('category', e.target.value)}>
              <option value="">Auto-detect</option>
              {categories.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </FormField>
          <FormField label="Env Var">
            <input className="form-input" placeholder="OPENAI_API_KEY" value={form.env_var} onChange={e => set('env_var', e.target.value)} />
          </FormField>
          <FormField label="Rotation (days)">
            <input className="form-input" type="number" placeholder="90" value={form.rotation_days} onChange={e => set('rotation_days', e.target.value)} />
          </FormField>
        </div>
        <FormField label="Expires">
          <input className="form-input" type="date" value={form.expires_at} onChange={e => set('expires_at', e.target.value)} />
        </FormField>
        <FormField label="Notes">
          <textarea className="form-input" rows={2} value={form.notes} onChange={e => set('notes', e.target.value)} />
        </FormField>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? <><div className="spinner" style={{ width: 14, height: 14 }} /> Saving…</> : 'Save to vault'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

function RotateModal({ target, onClose, onSaved, onError }: {
  target: ApiKey | null; onClose: () => void; onSaved: () => void; onError: (e: string) => void
}) {
  const [newKey, setNewKey] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!target) return
    setLoading(true)
    try {
      await rotateKey(target.id, newKey)
      onSaved()
      setNewKey('')
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : 'Error rotating key')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal open={!!target} onClose={onClose} title={`Rotate key: ${target?.name}`}>
      <form onSubmit={submit}>
        <FormField label="New API key value">
          <input className="form-input" type="password" placeholder="New key…" value={newKey} onChange={e => setNewKey(e.target.value)} required autoFocus />
        </FormField>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? 'Rotating…' : 'Rotate key'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
