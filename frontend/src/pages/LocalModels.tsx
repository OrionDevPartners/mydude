import { useState } from 'react'
import { getLocalModels, LocalProvider } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Badge, Empty } from '@/components/ui'
import { Cpu, ExternalLink, RefreshCw, CheckCircle, XCircle, Copy, Check, Database } from 'lucide-react'

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

      <RegistryPanel registry={data.registry} path={data.registry_path} exists={data.registry_exists} />
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

function RegistryPanel({ registry, path, exists }: { registry: Record<string, unknown>[]; path: string; exists: boolean }) {
  return (
    <Card style={{ padding: '18px 20px', marginTop: 6 }}>
      <p style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Database size={16} /> Local model registry
      </p>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14, fontFamily: 'monospace' }}>
        {path} {exists ? '' : '(not found)'}
      </p>
      {registry.length === 0
        ? <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
            No models registered. Local providers fall back to their default model. Create the registry file
            above to pin specific installed models.
          </p>
        : (
          <div className="glass-card" style={{ overflow: 'hidden' }}>
            <table className="data-table">
              <thead><tr><th>Model ID</th><th>Provider</th><th>Details</th></tr></thead>
              <tbody>
                {registry.map((m, i) => {
                  const { model_id, provider, ...rest } = m as Record<string, unknown>
                  return (
                    <tr key={i}>
                      <td style={{ fontFamily: 'monospace', fontSize: 12.5 }}>{String(model_id ?? '—')}</td>
                      <td><span className="badge badge-gray">{String(provider ?? '—')}</span></td>
                      <td style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
                        {Object.entries(rest).map(([k, v]) => `${k}: ${String(v)}`).join('  ·  ') || '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
    </Card>
  )
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
