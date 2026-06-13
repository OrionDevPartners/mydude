import { useState } from 'react'
import {
  getLocalModels, addLocalModel, updateLocalModel, removeLocalModel, LocalProvider,
  getLocalNodes, updateLocalNodes, testLocalNode, LocalNode, LocalNodeProbe,
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Badge, Empty } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
import { Cpu, ExternalLink, RefreshCw, CheckCircle, XCircle, Copy, Check, Database, Plus, Trash2, Pencil, X, Network, Wifi, Save } from 'lucide-react'

export function LocalModels() {
  const { data, loading, error, refetch } = useApi(getLocalModels, [])

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><Spinner /></div>
  if (error) return <Alert type="error">{error}</Alert>
  if (!data) return null

  const offline = data.total_count - data.reachable_count

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Local AI Models"
        subtitle="Run models on your own machine — sovereign, offline inference via Ollama and Apple MLX"
        actions={
          <button className="btn btn-secondary btn-sm" onClick={refetch} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 22 }}>
        <GlassStatCard value={data.reachable_count} label="Reachable" icon={<CheckCircle size={16} />} glow={data.reachable_count > 0} />
        <GlassStatCard value={offline} label="Offline" icon={<XCircle size={16} />} />
        <GlassStatCard value={data.total_count} label="Total servers" icon={<Cpu size={16} />} />
        <GlassStatCard value={data.registry?.length ?? 0} label="Registered models" icon={<Database size={16} />} />
      </div>

      <Card style={{ padding: '14px 18px', marginBottom: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <Cpu size={16} style={{ color: 'var(--accent)' }} />
          <span style={{ fontSize: 13.5, color: 'var(--text-primary)', fontWeight: 600 }}>
            {data.reachable_count} of {data.total_count} local server{data.total_count === 1 ? '' : 's'} reachable
          </span>
          <span style={{ fontSize: 12.5, color: 'var(--text-muted)', marginLeft: 'auto' }}>
            These providers run on your own hardware — this console can't install them remotely, but it
            gives you copy-ready setup commands and live status.
          </span>
        </div>
      </Card>

      {data.providers.length === 0
        ? <Empty message="No local providers configured." icon={<Cpu size={28} />} />
        : data.providers.map(p => <ProviderCard key={p.key} p={p} />)}

      <NodeEndpointsPanel onSaved={refetch} />

      <RegistryPanel
        registry={data.registry}
        path={data.registry_path}
        exists={data.registry_exists}
        providerKeys={data.providers.map(p => p.key)}
        onChange={refetch}
      />
    </div>
  )
}

function CopyCmd({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(cmd)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* clipboard unavailable */ }
  }
  return (
    <div className="copy-row">
      <code className="copy-cmd">{cmd}</code>
      <button className="btn btn-secondary btn-sm" onClick={copy} style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
        {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}

function ProviderCard({ p }: { p: LocalProvider }) {
  return (
    <Card style={{ padding: '18px 20px', marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 12 }}>
        <div>
          <p style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
            {p.reachable
              ? <CheckCircle size={16} style={{ color: '#34d399' }} />
              : <XCircle size={16} style={{ color: '#f87171' }} />}
            {p.label}
          </p>
          <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', maxWidth: 620 }}>{p.blurb}</p>
        </div>
        <Badge color={p.reachable ? 'green' : 'gray'}>{p.reachable ? 'reachable' : 'offline'}</Badge>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginBottom: 14 }}>
        <Meta label="Endpoint" value={p.base_url} mono />
        <Meta label="Default model" value={p.default_model || '—'} mono />
        <Meta label="Model env var" value={p.model_env || '—'} mono />
        <Meta label="Concurrency" value={String(p.concurrency)} />
      </div>

      {p.reachable && (
        <div style={{ marginBottom: 14 }}>
          <p className="form-label" style={{ marginBottom: 6 }}>Loaded models</p>
          {p.list_error
            ? <Alert type="warn">Server is up but model listing failed: {p.list_error}</Alert>
            : p.loaded_models && p.loaded_models.length > 0
              ? <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {p.loaded_models.map(m => <span key={m} className="badge badge-blue" style={{ fontFamily: 'monospace' }}>{m}</span>)}
                </div>
              : <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>No models loaded yet — pull one below.</p>}
        </div>
      )}

      {p.guidance && (
        <div>
          <p className="form-label" style={{ marginBottom: 8 }}>Setup</p>
          {p.guidance.install_cmd && (
            <div style={{ marginBottom: 8 }}>
              <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 4 }}>1 · Install</p>
              <CopyCmd cmd={p.guidance.install_cmd} />
            </div>
          )}
          {p.guidance.serve_cmd && (
            <div style={{ marginBottom: 8 }}>
              <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 4 }}>2 · Start the server</p>
              <CopyCmd cmd={p.guidance.serve_cmd} />
            </div>
          )}
          {p.guidance.pull_cmd && (
            <div style={{ marginBottom: 8 }}>
              <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 4 }}>3 · Pull / serve a model</p>
              <CopyCmd cmd={p.guidance.pull_cmd} />
            </div>
          )}
          {p.guidance.install_note && (
            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 8 }}>{p.guidance.install_note}</p>
          )}
          <div style={{ display: 'flex', gap: 14, marginTop: 10, flexWrap: 'wrap' }}>
            {p.guidance.install_url && (
              <a href={p.guidance.install_url} target="_blank" rel="noreferrer" style={linkStyle}>
                <ExternalLink size={13} /> Install guide
              </a>
            )}
            {p.guidance.models_url && (
              <a href={p.guidance.models_url} target="_blank" rel="noreferrer" style={linkStyle}>
                <ExternalLink size={13} /> Browse models
              </a>
            )}
          </div>
        </div>
      )}
    </Card>
  )
}

function NodeEndpointsPanel({ onSaved }: { onSaved: () => void }) {
  const { data, loading, error, refetch } = useApi(getLocalNodes, [])

  if (loading) return <Card style={{ padding: '18px 20px', marginTop: 6, marginBottom: 16 }}><Spinner /></Card>
  if (error) return <Card style={{ padding: '18px 20px', marginTop: 6, marginBottom: 16 }}><Alert type="error">{error}</Alert></Card>
  if (!data || data.nodes.length === 0) return null

  return <NodeEndpointsForm data={data} onSaved={() => { refetch(); onSaved() }} />
}

function NodeEndpointsForm({ data, onSaved }: {
  data: import('@/lib/api').LocalNodesData; onSaved: () => void
}) {
  // env-var -> value, seeded from the resolved configuration.
  const seed = (): Record<string, string> => {
    const v: Record<string, string> = {}
    v[data.shared_probe_timeout_env] = data.shared_probe_timeout
    for (const n of data.nodes) {
      if (n.base_url_env) v[n.base_url_env] = n.base_url
      v[n.probe_timeout_env] = n.probe_timeout
    }
    return v
  }
  const [vals, setVals] = useState<Record<string, string>>(seed)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [probes, setProbes] = useState<Record<string, LocalNodeProbe | 'testing'>>({})

  function set(key: string, value: string) {
    setVals(v => ({ ...v, [key]: value }))
  }

  function effectiveTimeout(n: LocalNode): string {
    const own = (vals[n.probe_timeout_env] || '').trim()
    if (own) return own
    const shared = (vals[data.shared_probe_timeout_env] || '').trim()
    if (shared) return shared
    return String(data.default_probe_timeout)
  }

  async function test(n: LocalNode) {
    const url = (vals[n.base_url_env] || '').trim()
    if (!url) { setProbes(p => ({ ...p, [n.key]: { ok: false, server_up: false, error: 'Enter an endpoint URL first.', timeout: 0 } })); return }
    setProbes(p => ({ ...p, [n.key]: 'testing' }))
    try {
      const res = await testLocalNode(url, effectiveTimeout(n))
      setProbes(p => ({ ...p, [n.key]: res }))
    } catch (e) {
      setProbes(p => ({ ...p, [n.key]: { ok: false, server_up: false, error: e instanceof Error ? e.message : 'Probe failed.', timeout: 0 } }))
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setErr(null); setMsg(null); setBusy(true)
    const payload: Record<string, string> = {}
    for (const [k, v] of Object.entries(vals)) payload[k] = (v || '').trim()
    try {
      await updateLocalNodes(payload)
      setMsg('Endpoint configuration saved — the swarm picks it up immediately.')
      onSaved()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save the configuration.')
    } finally { setBusy(false) }
  }

  return (
    <Card style={{ padding: '18px 20px', marginTop: 6, marginBottom: 16 }}>
      <p style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Network size={16} /> Mesh node endpoints
      </p>
      <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginBottom: 14, maxWidth: 660 }}>
        Point each local provider at its server. Use <code>http://localhost:…</code> for a server on this
        box, or a Cloudflare Mesh IP (e.g. <code>http://100.96.0.1:11434/v1</code>) for a remote node.
        Changes persist and apply immediately — no restart, no editing Secrets. Raise the probe timeout for
        Mesh hops (localhost is fast; cross-network needs more headroom).
      </p>

      {err && <Alert type="error">{err}</Alert>}
      {msg && <Alert type="success">{msg}</Alert>}

      <form onSubmit={save}>
        {data.nodes.map(n => {
          const probe = probes[n.key]
          return (
            <div key={n.key} className="glass-card" style={{ padding: 14, marginBottom: 12 }}>
              <p style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
                {n.label}
                {n.is_default && <span className="badge badge-gray">default endpoint</span>}
              </p>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <div style={{ flex: '2 1 300px' }}>
                  <label style={fieldLabel}>Endpoint URL <code style={envHint}>{n.base_url_env}</code></label>
                  <input
                    className="input"
                    placeholder={n.default_base_url}
                    value={vals[n.base_url_env] ?? ''}
                    onChange={e => set(n.base_url_env, e.target.value)}
                    style={{ width: '100%', fontFamily: 'monospace' }}
                  />
                </div>
                <div style={{ flex: '1 1 130px' }}>
                  <label style={fieldLabel}>Probe timeout (s) <code style={envHint}>{n.probe_timeout_env}</code></label>
                  <input
                    className="input"
                    placeholder={`shared / ${data.default_probe_timeout}`}
                    value={vals[n.probe_timeout_env] ?? ''}
                    onChange={e => set(n.probe_timeout_env, e.target.value)}
                    style={{ width: '100%' }}
                  />
                </div>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => test(n)}
                  disabled={probe === 'testing'}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0 }}
                >
                  <Wifi size={14} /> {probe === 'testing' ? 'Testing…' : 'Test connection'}
                </button>
              </div>
              {probe && probe !== 'testing' && (
                <div style={{ marginTop: 10 }}>
                  {probe.server_up
                    ? <span style={{ fontSize: 12.5, color: '#34d399', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <CheckCircle size={14} /> Reachable — {probe.host}:{probe.port} responded in {probe.latency_ms} ms
                      </span>
                    : <span style={{ fontSize: 12.5, color: '#f87171', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <XCircle size={14} /> Unreachable{probe.host ? ` — ${probe.host}:${probe.port}` : ''}{probe.error ? ` (${probe.error})` : ''}
                      </span>}
                </div>
              )}
            </div>
          )
        })}

        <div className="glass-card" style={{ padding: 14, marginBottom: 14 }}>
          <div style={{ flex: '1 1 200px', maxWidth: 280 }}>
            <label style={fieldLabel}>Shared probe timeout (s) <code style={envHint}>{data.shared_probe_timeout_env}</code></label>
            <input
              className="input"
              placeholder={String(data.default_probe_timeout)}
              value={vals[data.shared_probe_timeout_env] ?? ''}
              onChange={e => set(data.shared_probe_timeout_env, e.target.value)}
              style={{ width: '100%' }}
            />
          </div>
          <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 8 }}>
            Fallback timeout for any provider without its own override. Leave a field blank to revert it to
            the default ({data.default_probe_timeout}s).
          </p>
        </div>

        <button type="submit" className="btn btn-primary btn-sm" disabled={busy} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <Save size={14} /> {busy ? 'Saving…' : 'Save endpoints'}
        </button>
      </form>
    </Card>
  )
}

const envHint: React.CSSProperties = {
  fontSize: 10.5, color: 'var(--text-muted)', fontFamily: 'monospace', marginLeft: 4,
}

function RegistryPanel({ registry, path, exists, providerKeys, onChange }: {
  registry: Record<string, unknown>[]; path: string; exists: boolean;
  providerKeys: string[]; onChange: () => void
}) {
  const [modelId, setModelId] = useState('')
  const [provider, setProvider] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  async function add(e: React.FormEvent) {
    e.preventDefault()
    setErr(null); setMsg(null)
    const id = modelId.trim(), pv = provider.trim()
    if (!id || !pv) { setErr('Model ID and provider are both required.'); return }
    setBusy(true)
    try {
      await addLocalModel(id, pv)
      setMsg(`Added ${id} (${pv}) to the registry.`)
      setModelId(''); setProvider('')
      onChange()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not add the model.')
    } finally { setBusy(false) }
  }

  return (
    <Card style={{ padding: '18px 20px', marginTop: 6 }}>
      <p style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Database size={16} /> Local model registry
      </p>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14, fontFamily: 'monospace' }}>
        {path} {exists ? '' : '(will be created on first add)'}
      </p>

      {err && <Alert type="error">{err}</Alert>}
      {msg && <Alert type="success">{msg}</Alert>}

      {registry.length === 0
        ? <p style={{ fontSize: 12.5, color: 'var(--text-muted)', marginBottom: 14 }}>
            No models registered. Local providers fall back to their default model. Add one below to pin a
            specific installed model.
          </p>
        : (
          <div className="glass-card" style={{ overflow: 'hidden', marginBottom: 16 }}>
            <table className="data-table">
              <thead><tr><th>Model ID</th><th>Provider</th><th>Details</th><th></th></tr></thead>
              <tbody>
                {registry.map((m, i) => (
                  <RegistryRow
                    key={i}
                    entry={m as Record<string, unknown>}
                    providerKeys={providerKeys}
                    onChange={onChange}
                    onError={t => { setErr(t); setMsg(null) }}
                    onSuccess={t => { setMsg(t); setErr(null) }}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

      <p className="form-label" style={{ marginBottom: 8 }}>Add a model</p>
      <form onSubmit={add} style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div style={{ flex: '1 1 220px' }}>
          <label style={fieldLabel}>Model ID</label>
          <input
            className="input"
            placeholder="llama3.1:8b"
            value={modelId}
            onChange={e => setModelId(e.target.value)}
            style={{ width: '100%' }}
          />
        </div>
        <div style={{ flex: '1 1 160px' }}>
          <label style={fieldLabel}>Provider</label>
          <input
            className="input"
            placeholder="ollama"
            list="local-provider-options"
            value={provider}
            onChange={e => setProvider(e.target.value)}
            style={{ width: '100%' }}
          />
          <datalist id="local-provider-options">
            {providerKeys.map(k => <option key={k} value={k} />)}
          </datalist>
        </div>
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <Plus size={14} /> {busy ? 'Adding…' : 'Add to registry'}
        </button>
      </form>
      <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 8 }}>
        The model ID must match what the local server exposes (e.g. <code>llama3.1:8b</code> for Ollama). The
        loader picks up changes on the next refresh.
      </p>
    </Card>
  )
}

type MetaRow = { key: string; value: string }

function RegistryRow({ entry, providerKeys, onChange, onError, onSuccess }: {
  entry: Record<string, unknown>
  providerKeys: string[]
  onChange: () => void
  onError: (t: string) => void
  onSuccess: (t: string) => void
}) {
  const { model_id, provider: pv, ...rest } = entry
  const idStr = String(model_id ?? '')
  const pvStr = String(pv ?? '')
  const initialMeta = (): MetaRow[] =>
    Object.entries(rest).map(([k, v]) => ({ key: k, value: String(v) }))

  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [editId, setEditId] = useState(idStr)
  const [editProvider, setEditProvider] = useState(pvStr)
  const [meta, setMeta] = useState<MetaRow[]>(initialMeta)

  function startEdit() {
    setEditId(idStr)
    setEditProvider(pvStr)
    setMeta(initialMeta())
    setEditing(true)
  }

  async function save() {
    const id = editId.trim(), p = editProvider.trim()
    if (!id || !p) { onError('Model ID and provider are both required.'); return }
    const details: Record<string, string> = {}
    for (const { key, value } of meta) {
      const k = key.trim()
      if (!k || k === 'model_id' || k === 'provider') continue
      details[k] = value
    }
    setBusy(true)
    try {
      await updateLocalModel(idStr, pvStr, id, p, details)
      onSuccess(`Updated ${id} (${p}).`)
      setEditing(false)
      onChange()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Could not update the model.')
    } finally { setBusy(false) }
  }

  async function remove() {
    setRemoving(true)
    try {
      await removeLocalModel(idStr, pvStr)
      onSuccess(`Removed ${idStr} from the registry.`)
      onChange()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Could not remove the model.')
    } finally { setRemoving(false) }
  }

  if (editing) {
    return (
      <tr>
        <td colSpan={4} style={{ padding: 14 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <div style={{ flex: '1 1 220px' }}>
                <label style={fieldLabel}>Model ID</label>
                <input className="input" value={editId} onChange={e => setEditId(e.target.value)} style={{ width: '100%' }} />
              </div>
              <div style={{ flex: '1 1 160px' }}>
                <label style={fieldLabel}>Provider</label>
                <input className="input" list="local-provider-options" value={editProvider} onChange={e => setEditProvider(e.target.value)} style={{ width: '100%' }} />
              </div>
            </div>
            <div>
              <label style={fieldLabel}>Custom metadata (optional)</label>
              {meta.length === 0 && (
                <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 6 }}>
                  No metadata — add fields like <code>context_length</code> or <code>notes</code>.
                </p>
              )}
              {meta.map((row, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
                  <input
                    className="input"
                    placeholder="key (e.g. context_length)"
                    value={row.key}
                    onChange={e => setMeta(m => m.map((r, j) => j === i ? { ...r, key: e.target.value } : r))}
                    style={{ flex: '1 1 160px' }}
                  />
                  <input
                    className="input"
                    placeholder="value"
                    value={row.value}
                    onChange={e => setMeta(m => m.map((r, j) => j === i ? { ...r, value: e.target.value } : r))}
                    style={{ flex: '1 1 200px' }}
                  />
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => setMeta(m => m.filter((_, j) => j !== i))}
                    title="Remove field"
                    style={{ display: 'inline-flex', alignItems: 'center', flexShrink: 0 }}
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setMeta(m => [...m, { key: '', value: '' }])}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}
              >
                <Plus size={13} /> Add field
              </button>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={save} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <Check size={14} /> {busy ? 'Saving…' : 'Save changes'}
              </button>
              <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setEditing(false)}>
                Cancel
              </button>
            </div>
          </div>
        </td>
      </tr>
    )
  }

  return (
    <tr>
      <td style={{ fontFamily: 'monospace', fontSize: 12.5 }}>{idStr || '—'}</td>
      <td><span className="badge badge-gray">{pvStr || '—'}</span></td>
      <td style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
        {Object.entries(rest).map(([k, v]) => `${k}: ${String(v)}`).join('  ·  ') || '—'}
      </td>
      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
        <button
          className="btn btn-secondary btn-sm"
          onClick={startEdit}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}
        >
          <Pencil size={13} /> Edit
        </button>
        <button
          className="btn btn-secondary btn-sm"
          disabled={removing}
          onClick={remove}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginLeft: 6 }}
        >
          <Trash2 size={13} /> {removing ? 'Removing…' : 'Remove'}
        </button>
      </td>
    </tr>
  )
}

const fieldLabel: React.CSSProperties = {
  display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 4,
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 2 }}>{label}</p>
      <p style={{ fontSize: 12.5, color: 'var(--text-primary)', fontFamily: mono ? 'monospace' : 'inherit', wordBreak: 'break-all' }}>{value}</p>
    </div>
  )
}

const linkStyle: React.CSSProperties = {
  fontSize: 12.5, color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: 5, textDecoration: 'none',
}
