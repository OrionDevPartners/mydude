import { useState } from 'react'
import { getLocalModels, addLocalModel, removeLocalModel, LocalProvider } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Badge, Empty } from '@/components/ui'
import { Cpu, ExternalLink, RefreshCw, CheckCircle, XCircle, Copy, Check, Database, Plus, Trash2 } from 'lucide-react'

export function LocalModels() {
  const { data, loading, error, refetch } = useApi(getLocalModels, [])

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><Spinner /></div>
  if (error) return <Alert type="error">{error}</Alert>
  if (!data) return null

  return (
    <div>
      <PageHeader
        title="Local AI Models"
        subtitle="Run models on your own machine — sovereign, offline inference via Ollama and Apple MLX"
        actions={
          <button className="btn btn-secondary btn-sm" onClick={refetch} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />

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

function RegistryPanel({ registry, path, exists, providerKeys, onChange }: {
  registry: Record<string, unknown>[]; path: string; exists: boolean;
  providerKeys: string[]; onChange: () => void
}) {
  const [modelId, setModelId] = useState('')
  const [provider, setProvider] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [removing, setRemoving] = useState<string | null>(null)

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

  async function remove(id: string, pv: string) {
    setErr(null); setMsg(null)
    const tag = `${id}\u0000${pv}`
    setRemoving(tag)
    try {
      await removeLocalModel(id, pv)
      setMsg(`Removed ${id} from the registry.`)
      onChange()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not remove the model.')
    } finally { setRemoving(null) }
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
                {registry.map((m, i) => {
                  const { model_id, provider: pv, ...rest } = m as Record<string, unknown>
                  const idStr = String(model_id ?? ''), pvStr = String(pv ?? '')
                  const tag = `${idStr}\u0000${pvStr}`
                  return (
                    <tr key={i}>
                      <td style={{ fontFamily: 'monospace', fontSize: 12.5 }}>{idStr || '—'}</td>
                      <td><span className="badge badge-gray">{pvStr || '—'}</span></td>
                      <td style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
                        {Object.entries(rest).map(([k, v]) => `${k}: ${String(v)}`).join('  ·  ') || '—'}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <button
                          className="btn btn-secondary btn-sm"
                          disabled={removing === tag}
                          onClick={() => remove(idStr, pvStr)}
                          style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}
                        >
                          <Trash2 size={13} /> {removing === tag ? 'Removing…' : 'Remove'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
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
