import { useState } from 'react'
import {
  getSubscriptions, setSubStatus, setSubCredentials, deleteSub,
  openSub, cancelRequest, cancelConfirm, discoverSubscriptions,
  discoverEmailSubscriptions, addSubscription, Subscription, SubActionResult
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, Tabs, Modal, PageHeader, Empty, Screenshot, FormField } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import { Plus, Trash2, ExternalLink, XCircle, CreditCard, RefreshCw } from 'lucide-react'

const STATUS_COLOR: Record<string, string> = {
  confirmed: 'badge-green', candidate: 'badge-blue',
  dismissed: 'badge-gray', cancelled: 'badge-gray', cancel_pending: 'badge-yellow',
}

const SOURCE_LABEL: Record<string, string> = {
  browser_history: 'Browser history', email_receipt: 'Email receipt',
  manual: 'Manual', discovery: 'Discovery',
}

function formatSource(source: string): string {
  if (!source) return ''
  return source.split('+').map(s => SOURCE_LABEL[s] || s).filter(Boolean).join(' + ')
}

export function Subscriptions() {
  const [tab, setTab] = useState('Subscriptions')
  const { data, loading, error, refetch } = useApi(getSubscriptions, [])
  const [result, setResult] = useState<SubActionResult | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [creds, setCreds] = useState<Subscription | null>(null)
  const [confirmSub, setConfirmSub] = useState<Subscription | null>(null)
  const [confirmText, setConfirmText] = useState('')
  const [showAdd, setShowAdd] = useState(false)
  const [working, setWorking] = useState(false)

  async function action(fn: () => Promise<unknown>, successMsg?: string) {
    setWorking(true); setErr(null); setMsg(null)
    try {
      const r = await fn() as SubActionResult
      if (r && typeof r === 'object' && 'kind' in r) setResult(r)
      else if (successMsg) setMsg(successMsg)
      refetch()
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setWorking(false) }
  }

  return (
    <div>
      <PageHeader
        title="Subscriptions"
        subtitle="Discover and manage recurring subscriptions"
        actions={<button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}><Plus size={14} /> Add</button>}
      />
      {msg && <Alert type="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}
      {result && <SubResult result={result} onClose={() => setResult(null)} />}

      <Tabs tabs={['Subscriptions', 'Discover', 'Audit']} active={tab} onChange={setTab} />

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {tab === 'Subscriptions' && data && (
        data.subscriptions.length === 0
          ? <Empty message="No subscriptions yet. Use Discover to find them." icon={<CreditCard size={32} />} />
          : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {data.subscriptions.map(sub => (
                <Card key={sub.id} style={{ padding: '14px 18px' }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, flexWrap: 'wrap' }}>
                    <div style={{ flex: 1, minWidth: 180 }}>
                      <div style={{ display: 'flex', align: 'center', gap: 8, marginBottom: 4 }}>
                        <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>{sub.name}</span>
                        <span className={`badge ${STATUS_COLOR[sub.status] || 'badge-gray'}`}>{sub.status}</span>
                      </div>
                      <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                        {sub.domain} {sub.est_cost && `· ${sub.est_cost}`} {sub.login_username && `· ${sub.login_username}`} {sub.source && `· ${formatSource(sub.source)}`}
                      </p>
                    </div>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {sub.status === 'candidate' && (
                        <>
                          <button className="btn btn-secondary btn-sm" onClick={() => action(() => setSubStatus(sub.id, 'confirmed'), 'Confirmed')}>Confirm</button>
                          <button className="btn btn-ghost btn-sm" onClick={() => action(() => setSubStatus(sub.id, 'dismissed'), 'Dismissed')}>Dismiss</button>
                        </>
                      )}
                      {sub.login_url && (
                        <button className="btn btn-secondary btn-sm" onClick={() => action(() => openSub(sub.id))}>
                          <ExternalLink size={12} /> Open
                        </button>
                      )}
                      {sub.status === 'confirmed' && (
                        <button className="btn btn-secondary btn-sm" onClick={() => action(() => cancelRequest(sub.id))}>
                          <XCircle size={12} /> Cancel
                        </button>
                      )}
                      {sub.status === 'cancel_pending' && (
                        <button className="btn btn-danger btn-sm" onClick={() => { setConfirmSub(sub); setConfirmText('') }}>
                          Confirm cancel
                        </button>
                      )}
                      <button className="btn btn-ghost btn-sm" onClick={() => setCreds(sub)}>Credentials</button>
                      <button className="btn btn-ghost btn-sm" onClick={() => action(() => deleteSub(sub.id), 'Removed')}>
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )
      )}

      {tab === 'Discover' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Card style={{ padding: '18px 20px' }}>
            <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>Discover from browser history</p>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 14 }}>Read browser history over SSH to find subscription pages.</p>
            <div style={{ display: 'flex', gap: 8 }}>
              {['chrome', 'firefox', 'safari'].map(br => (
                <button key={br} className="btn btn-secondary btn-sm" disabled={working}
                  onClick={() => action(() => discoverSubscriptions(br))}>
                  {br}
                </button>
              ))}
            </div>
          </Card>
          <Card style={{ padding: '18px 20px' }}>
            <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>Discover from email receipts</p>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 14 }}>Scan billing emails via IMAP to find subscriptions.</p>
            <button className="btn btn-secondary btn-sm" disabled={working} onClick={() => action(() => discoverEmailSubscriptions())}>
              <RefreshCw size={13} /> Scan email
            </button>
          </Card>
        </div>
      )}

      {tab === 'Audit' && data && (
        data.audit.length === 0
          ? <Empty message="No audit actions yet." />
          : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead><tr><th>Subscription</th><th>Action</th><th>Status</th><th>Detail</th><th>Time</th></tr></thead>
                <tbody>
                  {data.audit.map((a, i) => (
                    <tr key={i}>
                      <td style={{ fontSize: 13, fontWeight: 500 }}>{a.subscription}</td>
                      <td style={{ fontSize: 12, fontFamily: 'monospace' }}>{a.action}</td>
                      <td><span className={`badge ${a.status === 'ok' ? 'badge-green' : 'badge-red'}`}>{a.status}</span></td>
                      <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{a.detail}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{fmtDate(a.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
      )}

      {/* Credentials modal */}
      <Modal open={!!creds} onClose={() => setCreds(null)} title={`Credentials: ${creds?.name}`}>
        {creds && <CredentialsForm sub={creds} onSaved={() => { setCreds(null); setMsg('Credentials saved'); refetch() }} onError={setErr} />}
      </Modal>

      {/* Confirm cancel modal */}
      <Modal open={!!confirmSub} onClose={() => setConfirmSub(null)} title="Confirm cancellation">
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
          Type <strong style={{ color: 'var(--accent)' }}>CANCEL</strong> to confirm cancellation of <strong style={{ color: 'var(--text-primary)' }}>{confirmSub?.name}</strong>. This action may be irreversible.
        </p>
        <input className="form-input" value={confirmText} onChange={e => setConfirmText(e.target.value)} placeholder="CANCEL" style={{ marginBottom: 14 }} />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={() => setConfirmSub(null)}>Cancel</button>
          <button className="btn btn-danger" disabled={confirmText !== 'CANCEL' || working}
            onClick={() => confirmSub && action(() => cancelConfirm(confirmSub.id, confirmText)).then(() => setConfirmSub(null))}>
            Confirm cancel
          </button>
        </div>
      </Modal>

      {/* Add subscription modal */}
      <AddSubModal open={showAdd} onClose={() => setShowAdd(false)} onSaved={() => { setShowAdd(false); setMsg('Subscription added'); refetch() }} onError={setErr} />
    </div>
  )
}

function SubResult({ result, onClose }: { result: SubActionResult; onClose: () => void }) {
  return (
    <div className={`alert ${result.ok ? 'alert-success' : 'alert-error'}`} style={{ marginBottom: 16, flexDirection: 'column', alignItems: 'flex-start', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
        <span>{result.message}</span>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit' }}>×</button>
      </div>
      {result.screenshot && <Screenshot b64={result.screenshot} />}
    </div>
  )
}

function CredentialsForm({ sub, onSaved, onError }: { sub: Subscription; onSaved: () => void; onError: (e: string) => void }) {
  const [form, setForm] = useState({ login_url: sub.login_url, account_url: sub.account_url, login_username: sub.login_username, password: '' })
  const [saving, setSaving] = useState(false)
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setSaving(true)
    try { await setSubCredentials(sub.id, form); onSaved() }
    catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }
  return (
    <form onSubmit={submit}>
      <FormField label="Login URL"><input className="form-input" type="url" value={form.login_url} onChange={e => set('login_url', e.target.value)} /></FormField>
      <FormField label="Account URL"><input className="form-input" type="url" value={form.account_url} onChange={e => set('account_url', e.target.value)} /></FormField>
      <FormField label="Username"><input className="form-input" value={form.login_username} onChange={e => set('login_username', e.target.value)} /></FormField>
      <FormField label="Password" hint="Stored encrypted in vault"><input className="form-input" type="password" value={form.password} onChange={e => set('password', e.target.value)} /></FormField>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4 }}>
        <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
      </div>
    </form>
  )
}

function AddSubModal({ open, onClose, onSaved, onError }: { open: boolean; onClose: () => void; onSaved: () => void; onError: (e: string) => void }) {
  const [form, setForm] = useState({ name: '', domain: '', login_url: '', account_url: '', login_username: '', est_cost: '', notes: '' })
  const [saving, setSaving] = useState(false)
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setSaving(true)
    try { await addSubscription(form); onSaved(); setForm({ name: '', domain: '', login_url: '', account_url: '', login_username: '', est_cost: '', notes: '' }) }
    catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }
  return (
    <Modal open={open} onClose={onClose} title="Add subscription">
      <form onSubmit={submit}>
        <FormField label="Name *"><input className="form-input" value={form.name} onChange={e => set('name', e.target.value)} required /></FormField>
        <FormField label="Domain"><input className="form-input" placeholder="netflix.com" value={form.domain} onChange={e => set('domain', e.target.value)} /></FormField>
        <FormField label="Monthly cost"><input className="form-input" placeholder="$9.99" value={form.est_cost} onChange={e => set('est_cost', e.target.value)} /></FormField>
        <FormField label="Notes"><textarea className="form-input" rows={2} value={form.notes} onChange={e => set('notes', e.target.value)} /></FormField>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Adding…' : 'Add'}</button>
        </div>
      </form>
    </Modal>
  )
}
