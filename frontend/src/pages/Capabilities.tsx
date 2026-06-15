import { useState } from 'react'
import {
  getCapabilities, toggleCapability, testBrowser, testSsh, testCode, testHistory,
  testReceipts, saveEmailConfig, saveSshConfig, TestResult,
  getCapabilityMatrix, reloadCapabilities, swapCapabilityTest,
  CapabilityProviderStatus, CapabilityCategoryStatus,
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, Tabs, PageHeader, Toggle, FormField, Screenshot } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
import { fmtDate } from '@/lib/utils'
import { Globe, Terminal, Mail, CheckCircle, XCircle, Zap, Grid, RefreshCw } from 'lucide-react'

const CATEGORY_LABELS: Record<string, string> = {
  llm: 'LLM',
  browser: 'Browser',
  database: 'Database',
  vector_search: 'Vector Search',
  knowledge_store: 'Knowledge Store',
  object_storage: 'Object Storage',
  secrets_vault: 'Secrets Vault',
  realtime: 'Realtime',
  orchestrator: 'Orchestrator',
  sig_optimizer: 'Sig Optimizer',
  container_compute: 'Container Compute',
}

export function Capabilities() {
  const [tab, setTab] = useState('Matrix')
  const { data, loading, error, refetch } = useApi(getCapabilities, [])
  const { data: matrix, loading: matrixLoading, error: matrixError, refetch: matrixRefetch } = useApi(getCapabilityMatrix, [])
  const [result, setResult] = useState<TestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [testErr, setTestErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [reloading, setReloading] = useState(false)
  const [swapResults, setSwapResults] = useState<Record<string, { ok: boolean; detail: string }>>({})
  const [swapTesting, setSwapTesting] = useState<string | null>(null)

  async function toggle(cap: string, current: boolean) {
    try {
      await toggleCapability(cap, !current)
      setMsg(`${cap} ${!current ? 'enabled' : 'disabled'}`)
      refetch()
    } catch (e: unknown) { setTestErr(e instanceof Error ? e.message : 'Error') }
  }

  async function runTest(fn: () => Promise<TestResult>) {
    setTesting(true); setResult(null); setTestErr(null)
    try { setResult(await fn()) }
    catch (e: unknown) { setTestErr(e instanceof Error ? e.message : 'Test failed') }
    finally { setTesting(false) }
  }

  async function handleSwapTest(category: string, key: string) {
    const id = `${category}:${key}`
    setSwapTesting(id)
    try {
      const res = await swapCapabilityTest(category, key)
      setSwapResults(prev => ({ ...prev, [id]: { ok: res.ok, detail: res.detail } }))
    } catch (e: unknown) {
      setSwapResults(prev => ({ ...prev, [id]: { ok: false, detail: e instanceof Error ? e.message : 'Swap test failed' } }))
    } finally {
      setSwapTesting(null)
    }
  }

  async function handleReload() {
    setReloading(true)
    try {
      await reloadCapabilities()
      setMsg('Capability registry reloaded')
      matrixRefetch()
    } catch (e: unknown) {
      setTestErr(e instanceof Error ? e.message : 'Reload failed')
    } finally {
      setReloading(false)
    }
  }

  const enabledCount = data ? [data.browser_enabled, data.ssh_enabled, data.email_enabled].filter(Boolean).length : 0
  const availableCount = matrix ? Object.values(matrix.matrix).filter(c => c.available_count > 0).length : 0
  const totalCategories = matrix ? matrix.categories.length : 11

  return (
    <div className="animate-fade-in">
      <PageHeader title="Capabilities Console" subtitle="Unified capability layer — all 11 provider categories" />
      {msg && <Alert type="success" onClose={() => setMsg(null)}>{msg}</Alert>}
      {testErr && <Alert type="error" onClose={() => setTestErr(null)}>{testErr}</Alert>}

      {matrix && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 22 }}>
          <GlassStatCard value={`${availableCount} / ${totalCategories}`} label="Categories live" icon={<Grid size={16} />} glow={availableCount > 0} />
          <GlassStatCard value={`${enabledCount} / 3`} label="Bridges enabled" icon={<Zap size={16} />} glow={enabledCount > 0} />
          <GlassStatCard value={data?.browser_enabled ? 'On' : 'Off'} label="Browser" icon={<Globe size={16} />} glow={!!data?.browser_enabled} />
          <GlassStatCard value={data?.ssh_enabled ? 'On' : 'Off'} label="SSH bridge" icon={<Terminal size={16} />} glow={!!data?.ssh_enabled} />
        </div>
      )}
      {data && !data.encryption_persistent && (
        <Alert type="error">
          <strong>ENCRYPTION_KEY is not set.</strong> Saved credentials will become undecryptable after a restart. Set{' '}
          <code>ENCRYPTION_KEY</code> as a persistent deployment secret.
        </Alert>
      )}

      <Tabs tabs={['Matrix', 'Browser', 'SSH', 'Email', 'Audit']} active={tab} onChange={setTab} />

      {/* ------------------------------------------------------------------ */}
      {/* MATRIX TAB — full 11-category capability matrix                    */}
      {/* ------------------------------------------------------------------ */}
      {tab === 'Matrix' && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 14 }}>
            <button
              className="btn btn-secondary btn-sm"
              onClick={handleReload}
              disabled={reloading}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}
            >
              <RefreshCw size={13} style={reloading ? { animation: 'spin 1s linear infinite' } : undefined} />
              {reloading ? 'Reloading…' : 'Reload registry'}
            </button>
          </div>
          {matrixLoading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
          {matrixError && <Alert type="error">{matrixError}</Alert>}
          {matrix && matrix.categories.map(cat => {
            const status: CapabilityCategoryStatus = matrix.matrix[cat] || { providers: [], active_key: null, enabled_count: 0, available_count: 0 }
            return (
              <Card key={cat} style={{ padding: '14px 18px', marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                      {CATEGORY_LABELS[cat] || cat}
                    </span>
                    {status.active_key && (
                      <span className="badge badge-green" style={{ fontSize: 11 }}>
                        active: {status.active_key}
                      </span>
                    )}
                    {!status.active_key && (
                      <span className="badge badge-gray" style={{ fontSize: 11 }}>unavailable</span>
                    )}
                  </div>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {status.available_count}/{status.enabled_count} providers live
                  </span>
                </div>
                {status.providers.length === 0 && (
                  <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0 }}>No providers configured.</p>
                )}
                {status.providers.map((p: CapabilityProviderStatus, i: number) => {
                  const swapId = `${cat}:${p.key}`
                  const swap = swapResults[swapId]
                  const swapBusy = swapTesting === swapId
                  return (
                  <div key={i} style={{
                    display: 'flex', alignItems: 'flex-start', gap: 10, padding: '7px 0',
                    borderTop: i > 0 ? '1px solid var(--border)' : undefined,
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                          {p.key}
                        </span>
                        <span className={`badge ${p.available ? 'badge-green' : 'badge-gray'}`} style={{ fontSize: 10.5 }}>
                          {p.available ? 'available' : 'unavailable'}
                        </span>
                        <span className="badge badge-gray" style={{ fontSize: 10.5 }}>{p.exec_locus}</span>
                        <span
                          className={`badge ${p.secrets_present ? 'badge-green' : 'badge-yellow'}`}
                          style={{ fontSize: 10.5 }}
                          title={p.secrets_present ? 'All required secrets are present' : 'One or more required secrets are missing'}
                        >
                          {p.secrets_present ? 'secrets ✓' : 'secrets missing'}
                        </span>
                        {p.required && <span className="badge" style={{ fontSize: 10.5, background: 'rgba(234,93,60,0.15)', color: '#e94560' }}>required</span>}
                        {p.cost > 0 && <span style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>cost={p.cost}</span>}
                      </div>
                      {p.health?.detail && (
                        <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '3px 0 0', lineHeight: 1.4 }}>
                          {p.health.detail}
                        </p>
                      )}
                      {swap && (
                        <p style={{
                          fontSize: 11, margin: '5px 0 0', lineHeight: 1.4,
                          display: 'flex', alignItems: 'flex-start', gap: 6,
                          color: swap.ok ? '#34d399' : '#fbbf24',
                        }}>
                          {swap.ok
                            ? <CheckCircle size={12} style={{ flexShrink: 0, marginTop: 1 }} />
                            : <XCircle size={12} style={{ flexShrink: 0, marginTop: 1 }} />}
                          <span>{swap.detail}</span>
                        </p>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleSwapTest(cat, p.key)}
                        disabled={swapBusy}
                        title="Prove a config-only swap to this provider would take effect with zero code change"
                        style={{ fontSize: 11, padding: '3px 9px' }}
                      >
                        {swapBusy ? 'Testing…' : 'Swap test'}
                      </button>
                      {p.available
                        ? <CheckCircle size={14} style={{ color: '#34d399' }} />
                        : <XCircle size={14} style={{ color: '#6b7280' }} />}
                    </div>
                  </div>
                  )
                })}
              </Card>
            )
          })}
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* BROWSER TAB                                                         */}
      {/* ------------------------------------------------------------------ */}
      {tab === 'Browser' && data && (
        <div>
          <Card style={{ padding: '18px 20px', marginBottom: 16 }}>
            <div style={{ display: 'flex', align: 'center', justifyContent: 'space-between', gap: 12 }}>
              <div>
                <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Globe size={15} /> Browser Automation
                </p>
                <p style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>Navigate and interact with websites via governed broker</p>
              </div>
              <Toggle checked={data.browser_enabled} onChange={() => toggle('browser', data.browser_enabled)} />
            </div>
          </Card>
          {data.browser_backends?.length > 0 && (
            <Card style={{ padding: '14px 18px', marginBottom: 16 }}>
              <p className="form-label" style={{ marginBottom: 8 }}>Backends</p>
              {(data.browser_backends as Array<Record<string,unknown>>).map((b, i) => (
                <div key={i} style={{ display: 'flex', align: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                  <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>{String(b.name || b.backend || i)}</span>
                  <span className={`badge ${b.available ? 'badge-green' : 'badge-gray'}`} style={{ marginLeft: 'auto' }}>
                    {b.available ? 'available' : 'unavailable'}
                  </span>
                </div>
              ))}
            </Card>
          )}
          <BrowserTestPanel onTest={url => runTest(() => testBrowser(url))} testing={testing} result={result} />
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* SSH TAB                                                             */}
      {/* ------------------------------------------------------------------ */}
      {tab === 'SSH' && data && (
        <div>
          <Card style={{ padding: '18px 20px', marginBottom: 16 }}>
            <div style={{ display: 'flex', align: 'center', justifyContent: 'space-between' }}>
              <div>
                <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Terminal size={15} /> SSH Bridge
                </p>
                <p style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
                  {data.ssh.configured ? `${data.ssh.user}@${data.ssh.host}:${data.ssh.port} (${data.ssh.auth})` : 'Not configured'}
                </p>
              </div>
              <Toggle checked={data.ssh_enabled} onChange={() => toggle('ssh', data.ssh_enabled)} />
            </div>
          </Card>
          <SshConfigForm onSaved={() => { setMsg('SSH config saved'); refetch() }} onError={setTestErr} />
          <SshTestPanel
            onRunTest={cmd => runTest(() => testSsh(cmd))}
            onCodeTest={() => runTest(() => testCode())}
            onHistoryTest={br => runTest(() => testHistory(br))}
            testing={testing} result={result}
          />
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* EMAIL TAB                                                           */}
      {/* ------------------------------------------------------------------ */}
      {tab === 'Email' && data && (
        <div>
          <Card style={{ padding: '18px 20px', marginBottom: 16 }}>
            <div style={{ display: 'flex', align: 'center', justifyContent: 'space-between' }}>
              <div>
                <p style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Mail size={15} /> Email / IMAP
                </p>
                <p style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
                  {data.email.configured ? `${data.email.user}@${data.email.host}:${data.email.port}` : 'Not configured'}
                </p>
              </div>
              <Toggle checked={data.email_enabled} onChange={() => toggle('email', data.email_enabled)} />
            </div>
          </Card>
          <EmailConfigForm onSaved={() => { setMsg('Email config saved'); refetch() }} onError={setTestErr} cfg={data.email} />
          <button className="btn btn-secondary" onClick={() => runTest(() => testReceipts())} disabled={testing}>
            {testing ? 'Testing…' : 'Test receipt discovery'}
          </button>
          {result && <ResultPanel result={result} />}
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* AUDIT TAB                                                           */}
      {/* ------------------------------------------------------------------ */}
      {tab === 'Audit' && data && (
        data.audit.length === 0
          ? <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>No audit events yet.</p>
          : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead><tr><th>Capability</th><th>Target</th><th>Backend</th><th>Status</th><th>Source</th><th>Time</th></tr></thead>
                <tbody>
                  {data.audit.map((a, i) => (
                    <tr key={i}>
                      <td style={{ fontSize: 12, fontFamily: 'monospace' }}>{a.capability}</td>
                      <td style={{ fontSize: 12, maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.target}</td>
                      <td style={{ fontSize: 12 }}>{a.backend}</td>
                      <td><span className={`badge ${a.status === 'allowed' || a.status === 'ok' ? 'badge-green' : 'badge-red'}`}>{a.status}</span></td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>{a.source}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{fmtDate(a.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
      )}
    </div>
  )
}

function ResultPanel({ result }: { result: TestResult }) {
  return (
    <Card style={{ padding: '14px 18px', marginTop: 14 }}>
      <div style={{ display: 'flex', align: 'center', gap: 8, marginBottom: 8 }}>
        {result.allowed
          ? <CheckCircle size={15} style={{ color: '#34d399' }} />
          : <XCircle size={15} style={{ color: '#f87171' }} />}
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
          {result.allowed ? 'Allowed' : 'Blocked'}
        </span>
        {result.reason && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>— {result.reason}</span>}
      </div>
      {result.output && <pre style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', background: 'rgba(0,0,0,0.2)', padding: 12, borderRadius: 8 }}>{result.output}</pre>}
      {result.screenshot && <Screenshot b64={result.screenshot} />}
    </Card>
  )
}

function BrowserTestPanel({ onTest, testing, result }: { onTest: (url: string) => void; testing: boolean; result: TestResult | null }) {
  const [url, setUrl] = useState('')
  return (
    <Card style={{ padding: '16px 18px' }}>
      <p className="form-label" style={{ marginBottom: 10 }}>Test browser</p>
      <div style={{ display: 'flex', gap: 10 }}>
        <input className="form-input" placeholder="https://example.com" value={url} onChange={e => setUrl(e.target.value)} style={{ flex: 1 }} />
        <button className="btn btn-primary" onClick={() => onTest(url)} disabled={testing || !url}>
          {testing ? 'Testing…' : 'Open'}
        </button>
      </div>
      {result && <ResultPanel result={result} />}
    </Card>
  )
}

function SshTestPanel({ onRunTest, onCodeTest, onHistoryTest, testing, result }: {
  onRunTest: (cmd: string) => void; onCodeTest: () => void; onHistoryTest: (br: string) => void;
  testing: boolean; result: TestResult | null
}) {
  const [cmd, setCmd] = useState('')
  const [browser, setBrowser] = useState('chrome')
  return (
    <Card style={{ padding: '16px 18px', marginBottom: 14 }}>
      <p className="form-label" style={{ marginBottom: 10 }}>Test SSH</p>
      <div style={{ display: 'flex', gap: 10, marginBottom: 10 }}>
        <input className="form-input" style={{ fontFamily: 'monospace', flex: 1 }} placeholder="echo hello" value={cmd} onChange={e => setCmd(e.target.value)} />
        <button className="btn btn-primary" onClick={() => onRunTest(cmd)} disabled={testing || !cmd}>Run</button>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button className="btn btn-secondary btn-sm" onClick={onCodeTest} disabled={testing}>Fetch code</button>
        <div style={{ display: 'flex', gap: 6 }}>
          <select className="form-input" style={{ width: 120 }} value={browser} onChange={e => setBrowser(e.target.value)}>
            <option value="chrome">Chrome</option><option value="firefox">Firefox</option><option value="safari">Safari</option>
          </select>
          <button className="btn btn-secondary btn-sm" onClick={() => onHistoryTest(browser)} disabled={testing}>Read history</button>
        </div>
      </div>
      {result && <ResultPanel result={result} />}
    </Card>
  )
}

function SshConfigForm({ onSaved, onError }: { onSaved: () => void; onError: (e: string) => void }) {
  const [form, setForm] = useState({ host: '', port: '22', user: '', private_key: '', password: '', host_fingerprint: '' })
  const [saving, setSaving] = useState(false)
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setSaving(true)
    try { await saveSshConfig(form); onSaved() }
    catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }
  return (
    <Card style={{ padding: '16px 18px', marginBottom: 14 }}>
      <p className="form-label" style={{ marginBottom: 12 }}>SSH configuration</p>
      <form onSubmit={submit}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 80px', gap: 10, marginBottom: 10 }}>
          <input className="form-input" placeholder="Host" value={form.host} onChange={e => set('host', e.target.value)} required />
          <input className="form-input" placeholder="Port" value={form.port} onChange={e => set('port', e.target.value)} />
        </div>
        <input className="form-input" style={{ marginBottom: 10 }} placeholder="Username" value={form.user} onChange={e => set('user', e.target.value)} required />
        <textarea className="form-input" style={{ marginBottom: 10, fontFamily: 'monospace', fontSize: 12 }} rows={3} placeholder="Private key (PEM)" value={form.private_key} onChange={e => set('private_key', e.target.value)} />
        <input className="form-input" style={{ marginBottom: 10 }} type="password" placeholder="Password (if no key)" value={form.password} onChange={e => set('password', e.target.value)} />
        <input className="form-input" style={{ marginBottom: 12 }} placeholder="Host fingerprint (optional)" value={form.host_fingerprint} onChange={e => set('host_fingerprint', e.target.value)} />
        <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>{saving ? 'Saving…' : 'Save SSH config'}</button>
      </form>
    </Card>
  )
}

function EmailConfigForm({ onSaved, onError, cfg }: { onSaved: () => void; onError: (e: string) => void; cfg: unknown }) {
  const c = cfg as Record<string, unknown>
  const [form, setForm] = useState({ host: String(c?.host || ''), port: String(c?.port || '993'), user: String(c?.user || ''), password: '', mailbox: String(c?.mailbox || 'INBOX') })
  const [saving, setSaving] = useState(false)
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setSaving(true)
    try { await saveEmailConfig(form); onSaved() }
    catch (err: unknown) { onError(err instanceof Error ? err.message : 'Error') }
    finally { setSaving(false) }
  }
  return (
    <Card style={{ padding: '16px 18px', marginBottom: 14 }}>
      <p className="form-label" style={{ marginBottom: 12 }}>IMAP configuration</p>
      <form onSubmit={submit}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 80px', gap: 10, marginBottom: 10 }}>
          <input className="form-input" placeholder="IMAP host" value={form.host} onChange={e => set('host', e.target.value)} required />
          <input className="form-input" placeholder="Port" value={form.port} onChange={e => set('port', e.target.value)} />
        </div>
        <input className="form-input" style={{ marginBottom: 10 }} placeholder="Email address" value={form.user} onChange={e => set('user', e.target.value)} required />
        <input className="form-input" style={{ marginBottom: 10 }} type="password" placeholder="Password / app password" value={form.password} onChange={e => set('password', e.target.value)} />
        <input className="form-input" style={{ marginBottom: 12 }} placeholder="Mailbox (INBOX)" value={form.mailbox} onChange={e => set('mailbox', e.target.value)} />
        <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>{saving ? 'Saving…' : 'Save email config'}</button>
      </form>
    </Card>
  )
}
