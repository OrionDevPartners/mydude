import { useState } from 'react'
import {
  getFinance, getFinanceTransactions, syncFinance, setFinanceAutosync,
  createFinanceProject, addFinanceBudget, attributeTransaction, createFinanceRule,
  setVendorDefaultProject, requestFinanceWrite, confirmFinanceWrite, rejectFinanceWrite,
  generateFinanceSuggestions,
  createPlaidLinkToken, exchangePlaidPublicToken, removePlaidItem,
  FinanceData, FinanceBudgetRow, FinanceProviderStatus, FinanceWrite, FinanceTxn, PlaidItemSummary,
} from '@/lib/api'
import { openPlaidLink } from '@/lib/plaid'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, Tabs, Modal, PageHeader, Empty, FormField, Toggle } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import {
  CircleDollarSign, RefreshCw, Plus, CheckCircle2, XCircle, AlertTriangle, Plug, Landmark, Trash2, Sparkles,
} from 'lucide-react'

const FLAG_LABEL: Record<string, { text: string; color: string }> = {
  over_budget: { text: 'Over budget', color: 'badge-red' },
  near_limit: { text: 'Near limit', color: 'badge-yellow' },
  no_budget: { text: 'No budget set', color: 'badge-gray' },
  large_txn: { text: 'Large txn', color: 'badge-yellow' },
}

const WRITE_STATUS_COLOR: Record<string, string> = {
  pending_confirm: 'badge-yellow', executed: 'badge-green',
  rejected: 'badge-gray', failed: 'badge-red',
}

function money(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
}

export function Finance() {
  const [tab, setTab] = useState('Overview')
  const { data, loading, error, refetch } = useApi(getFinance, [])
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
        title="Finance"
        subtitle="QuickBooks + Plaid — read-only by default, writes behind approval"
        actions={
          <button className="btn btn-primary btn-sm" disabled={working}
            onClick={() => action(async () => {
              const r = await syncFinance()
              const p = r.plaid as { ingested?: number } | undefined
              const q = r.quickbooks as { vendors?: number } | undefined
              setMsg(`Sync complete — ${p?.ingested ?? 0} transactions, ${q?.vendors ?? 0} vendors`)
            })}>
            <RefreshCw size={14} /> Sync now
          </button>
        }
      />
      {msg && <Alert type="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}

      <Tabs tabs={['Overview', 'Transactions', 'Projects', 'Approvals', 'Activity']} active={tab} onChange={setTab} />

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && tab === 'Overview' && <Overview data={data} working={working} action={action} setMsg={setMsg} setErr={setErr} refetch={refetch} />}
      {data && tab === 'Transactions' && <Transactions data={data} working={working} action={action} />}
      {data && tab === 'Projects' && <Projects data={data} working={working} action={action} setMsg={setMsg} setErr={setErr} refetch={refetch} />}
      {data && tab === 'Approvals' && <Approvals data={data} working={working} action={action} setMsg={setMsg} setErr={setErr} refetch={refetch} />}
      {data && tab === 'Activity' && <Activity data={data} />}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Overview
// --------------------------------------------------------------------------- //

function ProviderCard({ s }: { s: FinanceProviderStatus }) {
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

function PlaidCard({ s, working, connecting, onConnect, onDisconnect }: {
  s: FinanceProviderStatus; working: boolean; connecting: boolean
  onConnect: () => void; onDisconnect: (item: PlaidItemSummary) => void
}) {
  const items = s.items ?? []
  return (
    <Card style={{ padding: '14px 18px', flex: 1, minWidth: 260 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Plug size={15} style={{ opacity: 0.7 }} />
        <span style={{ fontSize: 14, fontWeight: 700, textTransform: 'capitalize' }}>{s.provider}</span>
        <span className={`badge ${s.connected ? 'badge-green' : 'badge-gray'}`}>
          {s.connected ? 'Connected' : 'Not connected'}
        </span>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>{s.detail}</p>

      {items.length > 0 && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {items.map(it => (
            <div key={it.item_id} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
              background: 'var(--bg-elevated, rgba(255,255,255,0.03))', borderRadius: 6,
            }}>
              <Landmark size={14} style={{ opacity: 0.7, flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {it.institution_name || it.item_id}
                </div>
                {it.status === 'error' && it.last_error && (
                  <div style={{ fontSize: 11, color: 'var(--danger, #e94560)' }}>{it.last_error}</div>
                )}
              </div>
              {it.status === 'error' && <span className="badge badge-red">Error</span>}
              {it.is_legacy
                ? <span className="badge badge-gray" title="Configured via PLAID_ACCESS_TOKEN env var">env</span>
                : (
                  <button className="btn btn-ghost btn-sm" disabled={working || it.id == null}
                    title="Disconnect" onClick={() => onDisconnect(it)}>
                    <Trash2 size={13} />
                  </button>
                )}
            </div>
          ))}
        </div>
      )}

      <button className="btn btn-secondary btn-sm" style={{ marginTop: 10 }}
        disabled={connecting} onClick={onConnect}>
        <Landmark size={14} /> {connecting ? 'Connecting…' : 'Connect bank'}
      </button>
    </Card>
  )
}

function BudgetCard({ row }: { row: FinanceBudgetRow }) {
  const pct = row.pct_used ?? 0
  const barColor = pct >= 100 ? 'var(--danger, #e94560)' : pct >= 80 ? '#e0a800' : 'var(--accent)'
  return (
    <Card style={{ padding: '14px 18px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
            {row.code} · {row.name}
          </div>
          {row.llc && <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{row.llc}</div>}
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {row.flags.map(f => (
            <span key={f} className={`badge ${FLAG_LABEL[f]?.color || 'badge-gray'}`}>{FLAG_LABEL[f]?.text || f}</span>
          ))}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 18, fontSize: 12, marginBottom: 8, flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--text-muted)' }}>Budget <strong style={{ color: 'var(--text-primary)' }}>{money(row.budget_total)}</strong></span>
        <span style={{ color: 'var(--text-muted)' }}>Actual <strong style={{ color: 'var(--text-primary)' }}>{money(row.actual_total)}</strong></span>
        <span style={{ color: 'var(--text-muted)' }}>Variance <strong style={{ color: row.variance < 0 ? 'var(--danger, #e94560)' : '#3fb950' }}>{money(row.variance)}</strong></span>
        <span style={{ color: 'var(--text-muted)' }}>{row.txn_count} txns</span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${Math.min(pct, 100)}%`, background: barColor }} />
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
        {row.pct_used === null ? 'No budget set' : `${row.pct_used}% used`}
      </div>
    </Card>
  )
}

function Overview({ data, working, action, setMsg, setErr, refetch }: {
  data: FinanceData; working: boolean; action: (fn: () => Promise<unknown>, m?: string) => void
  setMsg: (m: string | null) => void; setErr: (e: string | null) => void; refetch: () => void
}) {
  const [connecting, setConnecting] = useState(false)

  // Full Plaid Link flow: ask the backend for a link_token, open Plaid Link in
  // the browser, then post the resulting public_token back for a server-side
  // exchange. The access_token never touches the browser.
  async function connectBank() {
    setErr(null); setMsg(null); setConnecting(true)
    try {
      const { link_token } = await createPlaidLinkToken()
      const payload = await openPlaidLink(link_token)
      if (!payload) return // operator closed Link without finishing
      const res = await exchangePlaidPublicToken(payload)
      setMsg(`Connected ${res.institution_name || res.item_id}`)
      refetch()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Could not connect bank')
    } finally {
      setConnecting(false)
    }
  }

  function disconnectBank(item: PlaidItemSummary) {
    if (item.id == null) return // legacy env-configured item — managed via env var
    const label = item.institution_name || item.item_id
    if (!window.confirm(`Disconnect "${label}"? MyDude will revoke the token at Plaid and stop syncing this bank.`)) return
    action(async () => {
      const r = await removePlaidItem(item.id as number)
      setMsg(r.revoked_at_plaid ? `Disconnected ${label}` : (r.note || `Removed ${label}`))
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <ProviderCard s={data.providers.quickbooks} />
        <PlaidCard s={data.providers.plaid} working={working} connecting={connecting}
          onConnect={connectBank} onDisconnect={disconnectBank} />
      </div>

      <Card style={{ padding: '14px 18px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>Scheduled auto-sync</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Periodically pull transactions in the background (read-only).</div>
        </div>
        <Toggle checked={data.autosync_enabled} disabled={working}
          onChange={(v) => action(() => setFinanceAutosync(v), v ? 'Auto-sync enabled' : 'Auto-sync disabled')} />
      </Card>

      {data.budget.unattributed.count > 0 && (
        <Alert type="info">
          <AlertTriangle size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
          {data.budget.unattributed.count} unattributed transaction(s) totalling {money(data.budget.unattributed.total)} — review in the Transactions tab.
        </Alert>
      )}

      <div>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>
          Budget vs actuals ({data.budget.projects.length} projects)
        </div>
        {data.budget.projects.length === 0
          ? <Empty message="No projects yet. Add one in the Projects tab." icon={<CircleDollarSign size={32} />} />
          : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {data.budget.projects.map(row => <BudgetCard key={row.project_id} row={row} />)}
            </div>
          )}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Transactions
// --------------------------------------------------------------------------- //

const TXN_STATUS_COLOR: Record<string, string> = {
  attributed: 'badge-green', unattributed: 'badge-yellow', ignored: 'badge-gray',
}

function Transactions({ data, working, action }: {
  data: FinanceData; working: boolean; action: (fn: () => Promise<unknown>, m?: string) => void
}) {
  const [status, setStatus] = useState('')
  const { data: txns, loading, error, refetch } = useApi(
    () => getFinanceTransactions({ status: status || undefined, limit: 200 }), [status])

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
        {[['', 'All'], ['attributed', 'Attributed'], ['unattributed', 'Unattributed'], ['ignored', 'Ignored']].map(([val, label]) => (
          <button key={val} className={`btn btn-sm ${status === val ? 'btn-secondary' : 'btn-ghost'}`}
            onClick={() => setStatus(val)}>{label}</button>
        ))}
      </div>
      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 30 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}
      {txns && (txns.transactions.length === 0
        ? <Empty message="No transactions. Connect Plaid and run a sync." icon={<CircleDollarSign size={32} />} />
        : (
          <div className="glass-card" style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead><tr><th>Date</th><th>Name</th><th>Amount</th><th>Vendor</th><th>Status</th><th>Project</th></tr></thead>
              <tbody>
                {txns.transactions.map(t => (
                  <TxnRow key={t.id} t={t} projects={data.projects} working={working}
                    onAttribute={(pid) => action(async () => { await attributeTransaction(t.id, pid); refetch() }, 'Transaction updated')} />
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  )
}

function TxnRow({ t, projects, working, onAttribute }: {
  t: FinanceTxn; projects: FinanceData['projects']; working: boolean; onAttribute: (pid: string) => void
}) {
  return (
    <tr>
      <td style={{ fontSize: 12, whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>{t.date || '—'}{t.pending && <span className="badge badge-gray" style={{ marginLeft: 6 }}>pending</span>}</td>
      <td style={{ fontSize: 13 }}>{t.name || '—'}{t.memo && <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{t.memo}</div>}</td>
      <td style={{ fontSize: 13, fontFamily: 'monospace', whiteSpace: 'nowrap' }}>{money(t.amount)}</td>
      <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{t.vendor || '—'}</td>
      <td><span className={`badge ${TXN_STATUS_COLOR[t.attribution_status] || 'badge-gray'}`}>{t.attribution_status}</span>
        {t.attribution_method && <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>{t.attribution_method}</div>}</td>
      <td>
        <select className="form-input" style={{ fontSize: 12, padding: '4px 6px', minWidth: 130 }} disabled={working}
          value={t.project_id ?? ''} onChange={e => onAttribute(e.target.value)}>
          <option value="">— unattributed —</option>
          {projects.map(p => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}
        </select>
      </td>
    </tr>
  )
}

// --------------------------------------------------------------------------- //
// Projects, rules, vendors
// --------------------------------------------------------------------------- //

function Projects({ data, working, action, setMsg, setErr, refetch }: {
  data: FinanceData; working: boolean
  action: (fn: () => Promise<unknown>, m?: string) => void
  setMsg: (s: string | null) => void; setErr: (s: string | null) => void; refetch: () => void
}) {
  const [showProject, setShowProject] = useState(false)
  const [budgetFor, setBudgetFor] = useState<number | null>(null)
  const [showRule, setShowRule] = useState(false)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {/* Projects */}
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)' }}>Projects</span>
          <button className="btn btn-secondary btn-sm" onClick={() => setShowProject(true)}><Plus size={13} /> Add project</button>
        </div>
        {data.projects.length === 0
          ? <Empty message="No projects yet." />
          : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {data.projects.map(p => (
                <Card key={p.id} style={{ padding: '12px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <div>
                    <span style={{ fontSize: 13, fontWeight: 700 }}>{p.code} · {p.name}</span>
                    {p.llc && <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 8 }}>{p.llc}</span>}
                  </div>
                  <button className="btn btn-ghost btn-sm" onClick={() => setBudgetFor(p.id)}>Add budget</button>
                </Card>
              ))}
            </div>
          )}
      </div>

      {/* Attribution rules */}
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)' }}>Attribution rules</span>
          <button className="btn btn-secondary btn-sm" disabled={data.projects.length === 0} onClick={() => setShowRule(true)}><Plus size={13} /> Add rule</button>
        </div>
        {data.rules.length === 0
          ? <Empty message="No rules. Rules map vendor/memo text to a project." />
          : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead><tr><th>Match text</th><th>Project</th><th>Note</th></tr></thead>
                <tbody>
                  {data.rules.map(r => {
                    const p = data.projects.find(x => x.id === r.project_id)
                    return (
                      <tr key={r.id}>
                        <td style={{ fontSize: 13, fontFamily: 'monospace' }}>{r.match_text}</td>
                        <td style={{ fontSize: 12 }}>{p ? `${p.code} · ${p.name}` : `#${r.project_id}`}</td>
                        <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{r.note || '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {/* Vendors */}
      <div>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>Vendors & default project</div>
        {data.vendors.length === 0
          ? <Empty message="No vendors yet. They appear after a QuickBooks sync." />
          : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead><tr><th>Vendor</th><th>Source</th><th>Default project</th></tr></thead>
                <tbody>
                  {data.vendors.map(v => (
                    <tr key={v.id}>
                      <td style={{ fontSize: 13 }}>{v.name}</td>
                      <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{v.source}</td>
                      <td>
                        <select className="form-input" style={{ fontSize: 12, padding: '4px 6px', minWidth: 130 }} disabled={working}
                          value={v.default_project_id ?? ''}
                          onChange={e => action(() => setVendorDefaultProject(v.id, e.target.value), 'Vendor default updated')}>
                          <option value="">— none —</option>
                          {data.projects.map(p => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {/* Modals */}
      <AddProjectModal open={showProject} onClose={() => setShowProject(false)}
        onSaved={() => { setShowProject(false); setMsg('Project created'); refetch() }} onError={setErr} />
      <AddBudgetModal projectId={budgetFor} onClose={() => setBudgetFor(null)}
        onSaved={() => { setBudgetFor(null); setMsg('Budget added'); refetch() }} onError={setErr} />
      <AddRuleModal open={showRule} projects={data.projects} onClose={() => setShowRule(false)}
        onSaved={() => { setShowRule(false); setMsg('Rule created'); refetch() }} onError={setErr} />
    </div>
  )
}

function AddProjectModal({ open, onClose, onSaved, onError }: { open: boolean; onClose: () => void; onSaved: () => void; onError: (e: string) => void }) {
  const [form, setForm] = useState({ code: '', name: '', llc: '', description: '' })
  const [saving, setSaving] = useState(false)
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setSaving(true)
    try { await createFinanceProject(form); onSaved(); setForm({ code: '', name: '', llc: '', description: '' }) }
    catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }
  return (
    <Modal open={open} onClose={onClose} title="Add project">
      <form onSubmit={submit}>
        <FormField label="Code *"><input className="form-input" value={form.code} onChange={e => set('code', e.target.value)} required /></FormField>
        <FormField label="Name *"><input className="form-input" value={form.name} onChange={e => set('name', e.target.value)} required /></FormField>
        <FormField label="LLC / entity"><input className="form-input" value={form.llc} onChange={e => set('llc', e.target.value)} /></FormField>
        <FormField label="Description"><textarea className="form-input" rows={2} value={form.description} onChange={e => set('description', e.target.value)} /></FormField>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Add'}</button>
        </div>
      </form>
    </Modal>
  )
}

function AddBudgetModal({ projectId, onClose, onSaved, onError }: { projectId: number | null; onClose: () => void; onSaved: () => void; onError: (e: string) => void }) {
  const [form, setForm] = useState({ amount: '', category: '', period: 'total', notes: '' })
  const [saving, setSaving] = useState(false)
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); if (projectId === null) return
    setSaving(true)
    try { await addFinanceBudget(projectId, form); onSaved(); setForm({ amount: '', category: '', period: 'total', notes: '' }) }
    catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }
  return (
    <Modal open={projectId !== null} onClose={onClose} title="Add budget line">
      <form onSubmit={submit}>
        <FormField label="Amount *"><input className="form-input" type="number" step="0.01" value={form.amount} onChange={e => set('amount', e.target.value)} required /></FormField>
        <FormField label="Category"><input className="form-input" value={form.category} onChange={e => set('category', e.target.value)} /></FormField>
        <FormField label="Period"><input className="form-input" value={form.period} onChange={e => set('period', e.target.value)} placeholder="total" /></FormField>
        <FormField label="Notes"><textarea className="form-input" rows={2} value={form.notes} onChange={e => set('notes', e.target.value)} /></FormField>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Add'}</button>
        </div>
      </form>
    </Modal>
  )
}

function AddRuleModal({ open, projects, onClose, onSaved, onError }: {
  open: boolean; projects: FinanceData['projects']; onClose: () => void; onSaved: () => void; onError: (e: string) => void
}) {
  const [form, setForm] = useState({ match_text: '', project_id: '', note: '' })
  const [saving, setSaving] = useState(false)
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setSaving(true)
    try { await createFinanceRule(form); onSaved(); setForm({ match_text: '', project_id: '', note: '' }) }
    catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }
  return (
    <Modal open={open} onClose={onClose} title="Add attribution rule">
      <form onSubmit={submit}>
        <FormField label="Match text *" hint="Matched against vendor name and memo"><input className="form-input" value={form.match_text} onChange={e => set('match_text', e.target.value)} required /></FormField>
        <FormField label="Project *">
          <select className="form-input" value={form.project_id} onChange={e => set('project_id', e.target.value)} required>
            <option value="">— select —</option>
            {projects.map(p => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}
          </select>
        </FormField>
        <FormField label="Note"><input className="form-input" value={form.note} onChange={e => set('note', e.target.value)} /></FormField>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Add'}</button>
        </div>
      </form>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Approvals (gated write-back)
// --------------------------------------------------------------------------- //

function Approvals({ data, working, action, setMsg, setErr, refetch }: {
  data: FinanceData; working: boolean
  action: (fn: () => Promise<unknown>, m?: string) => Promise<boolean>
  setMsg: (s: string | null) => void; setErr: (s: string | null) => void; refetch: () => void
}) {
  const [showRequest, setShowRequest] = useState(false)
  const [confirmWrite, setConfirmWrite] = useState<FinanceWrite | null>(null)
  const [confirmText, setConfirmText] = useState('')
  const [generating, setGenerating] = useState(false)

  async function generate() {
    setGenerating(true); setMsg(null); setErr(null)
    try {
      const r = await generateFinanceSuggestions()
      if (!r.configured) {
        setErr(r.message || 'QuickBooks is not configured — connect it to generate suggestions.')
      } else {
        const skips = Object.values(r.skipped || {}).reduce((a, n) => a + n, 0)
        setMsg(`Generated ${r.counts.total} suggestion${r.counts.total === 1 ? '' : 's'} `
          + `(${r.counts.categorize} categorize, ${r.counts.create_bill} bill)`
          + `${skips ? ` · ${skips} skipped` : ''} — review and confirm below.`)
      }
      refetch()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed to generate suggestions')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          Write-backs to QuickBooks require explicit confirmation before they execute.
        </span>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn-secondary btn-sm" onClick={generate} disabled={generating || working}>
            <Sparkles size={13} /> {generating ? 'Generating…' : 'Generate suggestions'}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => setShowRequest(true)}><Plus size={13} /> Request write</button>
        </div>
      </div>

      {data.writes.length === 0
        ? <Empty message="No write requests yet." />
        : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.writes.map(w => (
              <Card key={w.id} style={{ padding: '12px 16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                      <span style={{ fontSize: 13, fontWeight: 700, fontFamily: 'monospace' }}>{w.kind}</span>
                      <span className={`badge ${WRITE_STATUS_COLOR[w.status] || 'badge-gray'}`}>{w.status}</span>
                      {w.summary?.startsWith('Auto-suggested') && <span className="badge badge-purple">auto-suggested</span>}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{w.summary || '—'}</div>
                    {w.result_detail && <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>{w.result_detail}</div>}
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>Requested {fmtDate(w.requested_at)}</div>
                  </div>
                  {w.status === 'pending_confirm' && (
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-danger btn-sm" onClick={() => { setConfirmWrite(w); setConfirmText('') }}>
                        <CheckCircle2 size={12} /> Confirm
                      </button>
                      <button className="btn btn-ghost btn-sm" onClick={() => action(() => rejectFinanceWrite(w.id), 'Write rejected')}>
                        <XCircle size={12} /> Reject
                      </button>
                    </div>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}

      {/* Confirm modal */}
      <Modal open={!!confirmWrite} onClose={() => setConfirmWrite(null)} title="Confirm write-back">
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
          Type <strong style={{ color: 'var(--accent)' }}>CONFIRM</strong> to execute <strong style={{ color: 'var(--text-primary)' }}>{confirmWrite?.kind}</strong> against QuickBooks. This is an outbound, irreversible action.
        </p>
        <input className="form-input" value={confirmText} onChange={e => setConfirmText(e.target.value)} placeholder="CONFIRM" style={{ marginBottom: 14 }} />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={() => setConfirmWrite(null)}>Cancel</button>
          <button className="btn btn-danger" disabled={confirmText !== 'CONFIRM' || working}
            onClick={() => confirmWrite && action(async () => {
              const r = await confirmFinanceWrite(confirmWrite.id, confirmText)
              if (!r.ok) throw new Error(r.message || 'Confirmation failed')
            }, 'Write executed').then(ok => { if (ok) setConfirmWrite(null) })}>
            Execute
          </button>
        </div>
      </Modal>

      <RequestWriteModal open={showRequest} onClose={() => setShowRequest(false)}
        onSaved={() => { setShowRequest(false); setMsg('Write request created — confirm to execute'); refetch() }} onError={setErr} />
    </div>
  )
}

function RequestWriteModal({ open, onClose, onSaved, onError }: { open: boolean; onClose: () => void; onSaved: () => void; onError: (e: string) => void }) {
  const [form, setForm] = useState({ kind: 'create_bill', target_external_id: '', summary: '', payload: '' })
  const [saving, setSaving] = useState(false)
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setSaving(true)
    try { await requestFinanceWrite(form); onSaved(); setForm({ kind: 'create_bill', target_external_id: '', summary: '', payload: '' }) }
    catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }
  return (
    <Modal open={open} onClose={onClose} title="Request QuickBooks write">
      <form onSubmit={submit}>
        <FormField label="Kind *">
          <select className="form-input" value={form.kind} onChange={e => set('kind', e.target.value)}>
            <option value="create_bill">create_bill</option>
            <option value="create_invoice">create_invoice</option>
            <option value="categorize">categorize</option>
          </select>
        </FormField>
        <FormField label="Target external ID" hint="QuickBooks entity id, if updating"><input className="form-input" value={form.target_external_id} onChange={e => set('target_external_id', e.target.value)} /></FormField>
        <FormField label="Summary"><input className="form-input" value={form.summary} onChange={e => set('summary', e.target.value)} /></FormField>
        <FormField label="Payload (JSON) *" hint="The QuickBooks request body"><textarea className="form-input" rows={5} style={{ fontFamily: 'monospace', fontSize: 12 }} value={form.payload} onChange={e => set('payload', e.target.value)} required placeholder='{"Line": [...]}' /></FormField>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Create request'}</button>
        </div>
      </form>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Activity
// --------------------------------------------------------------------------- //

function Activity({ data }: { data: FinanceData }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>Recent sync runs</div>
        {data.recent_runs.length === 0
          ? <Empty message="No syncs yet." />
          : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead><tr><th>Source</th><th>Trigger</th><th>Status</th><th>Ingested</th><th>Attributed</th><th>Removed</th><th>When</th></tr></thead>
                <tbody>
                  {data.recent_runs.map(r => (
                    <tr key={r.id}>
                      <td style={{ fontSize: 12, fontFamily: 'monospace' }}>{r.source}</td>
                      <td style={{ fontSize: 12 }}>{r.trigger}</td>
                      <td><span className={`badge ${r.status === 'ok' ? 'badge-green' : r.status === 'error' ? 'badge-red' : 'badge-gray'}`}>{r.status}</span>{r.error && <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{r.error}</div>}</td>
                      <td style={{ fontSize: 12 }}>{r.transactions_ingested + r.entities_ingested}</td>
                      <td style={{ fontSize: 12 }}>{r.attributed_count}</td>
                      <td style={{ fontSize: 12 }}>{r.removed_count}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{fmtDate(r.finished_at || r.started_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      <div>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 10 }}>Audit log</div>
        {data.audit.length === 0
          ? <Empty message="No audit entries yet." />
          : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead><tr><th>Action</th><th>Status</th><th>Detail</th><th>When</th></tr></thead>
                <tbody>
                  {data.audit.map(a => (
                    <tr key={a.id}>
                      <td style={{ fontSize: 12, fontFamily: 'monospace' }}>{a.action}</td>
                      <td><span className={`badge ${a.status === 'ok' ? 'badge-green' : a.status === 'error' || a.status === 'failed' ? 'badge-red' : 'badge-yellow'}`}>{a.status}</span></td>
                      <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>{a.detail || '—'}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{fmtDate(a.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
    </div>
  )
}
