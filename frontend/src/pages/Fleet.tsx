import { useState } from 'react'
import {
  getFleetStatus, listBots, createBot, startBot, stopBot, deleteBot,
  listTeams, createTeam, startTeam, stopTeam, deleteTeam, scaleTeam,
  listProvisioning, planProvision, approveProvision,
  FleetBot, FleetTeam, ProvisioningJob, ProvisionedResource,
} from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, Tabs, PageHeader, Badge, Empty } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import {
  Bot, Users, Server, Play, Square, Trash2, Plus, ChevronDown, ChevronRight,
  CheckCircle, AlertCircle, Clock, Zap, Package, GitBranch, Cpu, TrendingUp
} from 'lucide-react'

const LIFECYCLE_COLOR: Record<string, string> = {
  defined: 'gray', running: 'green', stopped: 'orange', failed: 'red',
  provisioning: 'blue', error: 'red',
}
const PROVISION_COLOR: Record<string, string> = {
  planned: 'gray', pending_approval: 'orange', awaiting_approval: 'orange',
  provisioning: 'blue', active: 'green', failed: 'red', destroyed: 'gray',
  planning: 'gray', applying: 'blue', done: 'green',
}

function StatusBadge({ status }: { status: string }) {
  const color = LIFECYCLE_COLOR[status] || PROVISION_COLOR[status] || 'gray'
  return <Badge color={color}>{status}</Badge>
}

// ---- Bot Builder ----
function BotBuilder({ teams, onCreated }: { teams: FleetTeam[]; onCreated: () => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [goal, setGoal] = useState('')
  const [teamId, setTeamId] = useState('')
  const [role, setRole] = useState('')
  const [personality, setPersonality] = useState('')
  const [protocols, setProtocols] = useState('')
  const [caps, setCaps] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) { setErr('Name is required'); return }
    setLoading(true); setErr(null)
    try {
      const identity = role ? { role, personality } : {}
      const promptCards = role ? [`Act as ${role}.`, personality ? `Personality: ${personality}` : ''].filter(Boolean) : []
      const protocolList = protocols.split('\n').map(s => s.trim()).filter(Boolean)
      const capList = caps.split(',').map(s => s.trim()).filter(Boolean)
      await createBot({
        name: name.trim(),
        description: description.trim(),
        goal: goal.trim(),
        team_id: teamId || undefined,
        identity_schema: JSON.stringify(identity),
        prompt_cards: JSON.stringify(promptCards),
        protocols: JSON.stringify(protocolList),
        allowed_caps: JSON.stringify(capList),
      })
      setName(''); setDescription(''); setGoal(''); setTeamId(''); setRole(''); setPersonality(''); setProtocols(''); setCaps('')
      setOpen(false)
      onCreated()
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setLoading(false) }
  }

  return (
    <div style={{ marginBottom: 16 }}>
      <button className="btn btn-primary btn-sm" onClick={() => setOpen(!open)} style={{ gap: 7 }}>
        <Plus size={14} /> New Bot
      </button>
      {open && (
        <Card style={{ marginTop: 12, padding: '18px 20px' }}>
          <p style={{ fontSize: 14, fontWeight: 700, marginBottom: 14, color: 'var(--text-primary)' }}>Define New Bot</p>
          {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}
          <form onSubmit={submit}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
              <div>
                <label className="form-label">Name *</label>
                <input className="form-input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. ResearchBot" />
              </div>
              <div>
                <label className="form-label">Team (optional)</label>
                <select className="form-input" value={teamId} onChange={e => setTeamId(e.target.value)}>
                  <option value="">— solo bot —</option>
                  {teams.map(t => <option key={t.id} value={String(t.id)}>{t.name}</option>)}
                </select>
              </div>
            </div>
            <div style={{ marginBottom: 12 }}>
              <label className="form-label">Goal</label>
              <textarea className="form-input" rows={2} value={goal} onChange={e => setGoal(e.target.value)} placeholder="What should this bot accomplish?" style={{ resize: 'vertical' }} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
              <div>
                <label className="form-label">Role / Identity</label>
                <input className="form-input" value={role} onChange={e => setRole(e.target.value)} placeholder="e.g. Lead Researcher" />
              </div>
              <div>
                <label className="form-label">Personality traits</label>
                <input className="form-input" value={personality} onChange={e => setPersonality(e.target.value)} placeholder="e.g. Analytical, skeptical" />
              </div>
            </div>
            <div style={{ marginBottom: 12 }}>
              <label className="form-label">Operator protocols (one per line)</label>
              <textarea className="form-input" rows={2} value={protocols} onChange={e => setProtocols(e.target.value)} placeholder="Always ask before taking irreversible actions.&#10;Report findings in bullet points." style={{ resize: 'vertical' }} />
            </div>
            <div style={{ marginBottom: 14 }}>
              <label className="form-label">Allowed capabilities (comma-separated)</label>
              <input className="form-input" value={caps} onChange={e => setCaps(e.target.value)} placeholder="e.g. git_status, browser_open" />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="submit" className="btn btn-primary btn-sm" disabled={loading}>{loading ? <Spinner size={14} /> : 'Create Bot'}</button>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>Cancel</button>
            </div>
          </form>
        </Card>
      )}
    </div>
  )
}

// ---- Team Builder ----
function TeamBuilder({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [spawnCap, setSpawnCap] = useState('5')
  const [namespace, setNamespace] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) { setErr('Name is required'); return }
    setLoading(true); setErr(null)
    try {
      await createTeam({ name: name.trim(), description: description.trim(), spawn_cap: spawnCap, memory_namespace: namespace.trim() })
      setName(''); setDescription(''); setSpawnCap('5'); setNamespace('')
      setOpen(false); onCreated()
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setLoading(false) }
  }

  return (
    <div style={{ marginBottom: 16 }}>
      <button className="btn btn-primary btn-sm" onClick={() => setOpen(!open)} style={{ gap: 7 }}>
        <Plus size={14} /> New Team
      </button>
      {open && (
        <Card style={{ marginTop: 12, padding: '18px 20px' }}>
          <p style={{ fontSize: 14, fontWeight: 700, marginBottom: 14, color: 'var(--text-primary)' }}>Create Team</p>
          {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}
          <form onSubmit={submit}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
              <div>
                <label className="form-label">Name *</label>
                <input className="form-input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Research Squad" />
              </div>
              <div>
                <label className="form-label">Spawn cap (max bots)</label>
                <input className="form-input" type="number" min={1} max={50} value={spawnCap} onChange={e => setSpawnCap(e.target.value)} />
              </div>
            </div>
            <div style={{ marginBottom: 12 }}>
              <label className="form-label">Description</label>
              <input className="form-input" value={description} onChange={e => setDescription(e.target.value)} placeholder="What does this team do?" />
            </div>
            <div style={{ marginBottom: 14 }}>
              <label className="form-label">Memory namespace (optional)</label>
              <input className="form-input" value={namespace} onChange={e => setNamespace(e.target.value)} placeholder="e.g. team:research (auto-assigned if blank)" />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="submit" className="btn btn-primary btn-sm" disabled={loading}>{loading ? <Spinner size={14} /> : 'Create Team'}</button>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>Cancel</button>
            </div>
          </form>
        </Card>
      )}
    </div>
  )
}

// ---- Provisioning Panel ----
function ProvisionPanel({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false)
  const [rtype, setRtype] = useState('git_repo')
  const [rname, setRname] = useState('')
  const [botId, setBotId] = useState('')
  const [teamId, setTeamId] = useState('')
  const [extra, setExtra] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<{ plan_output?: string; job_id?: number } | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const RESOURCE_ICONS: Record<string, React.ReactNode> = {
    vm: <Server size={14} />, git_repo: <GitBranch size={14} />, ml_service: <Cpu size={14} />,
  }

  async function planIt(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true); setErr(null); setResult(null)
    try {
      const cfg: Record<string, string> = { name: rname.trim() || `${rtype}-bot`, ...(extra ? JSON.parse(extra) : {}) }
      const r = await planProvision({
        resource_type: rtype,
        config: JSON.stringify(cfg),
        bot_id: botId || undefined,
        team_id: teamId || undefined,
      })
      setResult(r)
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Plan failed') }
    finally { setLoading(false) }
  }

  async function approve(jobId: number) {
    setLoading(true); setErr(null)
    try {
      await approveProvision(jobId)
      setResult(null); setOpen(false); onDone()
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Apply failed') }
    finally { setLoading(false) }
  }

  return (
    <div style={{ marginBottom: 16 }}>
      <button className="btn btn-sm" style={{ gap: 7, background: 'var(--surface-2)' }} onClick={() => setOpen(!open)}>
        <Package size={14} /> Provision Resource
      </button>
      {open && (
        <Card style={{ marginTop: 12, padding: '18px 20px' }}>
          <p style={{ fontSize: 14, fontWeight: 700, marginBottom: 14, color: 'var(--text-primary)' }}>Cloud Provisioning</p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>
            Creates a plan for operator review before any real resources are created.
          </p>
          {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}

          {!result ? (
            <form onSubmit={planIt}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                <div>
                  <label className="form-label">Resource type</label>
                  <select className="form-input" value={rtype} onChange={e => setRtype(e.target.value)}>
                    <option value="git_repo">Git Repository</option>
                    <option value="vm">Virtual Machine (EC2)</option>
                    <option value="ml_service">ML Service (MLflow/SageMaker)</option>
                  </select>
                </div>
                <div>
                  <label className="form-label">Resource name</label>
                  <input className="form-input" value={rname} onChange={e => setRname(e.target.value)} placeholder={`my-${rtype}`} />
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                <div>
                  <label className="form-label">For bot ID (optional)</label>
                  <input className="form-input" value={botId} onChange={e => setBotId(e.target.value)} placeholder="bot id" />
                </div>
                <div>
                  <label className="form-label">For team ID (optional)</label>
                  <input className="form-input" value={teamId} onChange={e => setTeamId(e.target.value)} placeholder="team id" />
                </div>
              </div>
              <div style={{ marginBottom: 14 }}>
                <label className="form-label">Extra config (JSON, optional)</label>
                <input className="form-input" value={extra} onChange={e => setExtra(e.target.value)} placeholder='{"region":"us-east-1"}' />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button type="submit" className="btn btn-primary btn-sm" disabled={loading}>{loading ? <Spinner size={14} /> : 'Generate Plan'}</button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>Cancel</button>
              </div>
            </form>
          ) : (
            <div>
              <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>
                Plan Output <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(job #{result.job_id})</span>
              </p>
              <pre style={{ background: 'var(--surface-2)', padding: 12, borderRadius: 6, fontSize: 11.5, overflowX: 'auto', marginBottom: 14, whiteSpace: 'pre-wrap', color: 'var(--text-secondary)' }}>
                {result.plan_output}
              </pre>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button
                  className="btn btn-primary btn-sm"
                  disabled={loading}
                  onClick={() => result.job_id && approve(result.job_id)}
                >
                  {loading ? <Spinner size={14} /> : <><CheckCircle size={13} /> Approve &amp; Apply</>}
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => setResult(null)}>Revise</button>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Review carefully — some resources may incur cloud costs.</span>
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

// ---- Bot Card ----
function BotCard({ bot, onAction }: { bot: FleetBot; onAction: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [goalInput, setGoalInput] = useState('')

  async function start() {
    setLoading(true); setErr(null)
    try { await startBot(bot.id, goalInput); onAction() }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setLoading(false) }
  }

  async function stop() {
    setLoading(true); setErr(null)
    try { await stopBot(bot.id); onAction() }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setLoading(false) }
  }

  async function del() {
    if (!confirm(`Delete bot "${bot.name}"?`)) return
    setLoading(true); setErr(null)
    try { await deleteBot(bot.id); onAction() }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setLoading(false) }
  }

  return (
    <Card style={{ padding: '14px 18px', marginBottom: 10 }}>
      {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Bot size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text-primary)' }}>{bot.name}</span>
            <StatusBadge status={bot.lifecycle} />
            {bot.spawned_by_id && <Badge color="purple">spawned</Badge>}
            {bot.team_id && <Badge color="gray">team #{bot.team_id}</Badge>}
          </div>
          {bot.goal && <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{bot.goal}</p>}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setExpanded(!expanded)}
            style={{ padding: '4px 8px' }}
          >
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
          {bot.lifecycle !== 'running' && (
            <button className="btn btn-primary btn-sm" disabled={loading} onClick={start} style={{ gap: 5 }}>
              <Play size={12} /> Start
            </button>
          )}
          {bot.lifecycle === 'running' && (
            <button className="btn btn-sm" disabled={loading} onClick={stop} style={{ gap: 5, background: 'var(--surface-2)' }}>
              <Square size={12} /> Stop
            </button>
          )}
          <button className="btn btn-ghost btn-sm" disabled={loading} onClick={del} style={{ padding: '4px 7px', color: 'var(--text-muted)' }}>
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      {expanded && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 10 }}>
            {bot.identity_schema && Object.keys(bot.identity_schema).length > 0 && (
              <div>
                <p className="form-label" style={{ marginBottom: 4 }}>Identity</p>
                <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{JSON.stringify(bot.identity_schema, null, 2)}</p>
              </div>
            )}
            {bot.allowed_caps && bot.allowed_caps.length > 0 && (
              <div>
                <p className="form-label" style={{ marginBottom: 4 }}>Capabilities</p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {bot.allowed_caps.map(c => <Badge key={c} color="blue">{c}</Badge>)}
                </div>
              </div>
            )}
          </div>
          {bot.protocols && bot.protocols.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <p className="form-label" style={{ marginBottom: 4 }}>Protocols</p>
              <ul style={{ fontSize: 12, color: 'var(--text-secondary)', paddingLeft: 16, margin: 0 }}>
                {bot.protocols.map((p, i) => <li key={i}>{p}</li>)}
              </ul>
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
            <input
              className="form-input"
              style={{ flex: 1, fontSize: 12 }}
              value={goalInput}
              onChange={e => setGoalInput(e.target.value)}
              placeholder="Override goal for this run (optional)"
            />
          </div>
          {bot.last_run_at && (
            <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 8 }}>
              Last run: {fmtDate(bot.last_run_at)}
              {bot.last_task_run_id && <> · Task #{bot.last_task_run_id}</>}
            </p>
          )}
        </div>
      )}
    </Card>
  )
}

// ---- Team Card ----
function TeamCard({ team, bots, onAction }: { team: FleetTeam; bots: FleetBot[]; onAction: () => void }) {
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [scaleOpen, setScaleOpen] = useState(false)
  const [targetCount, setTargetCount] = useState('')
  const [scaleMsg, setScaleMsg] = useState<string | null>(null)
  const teamBots = bots.filter(b => b.team_id === team.id)

  async function start() {
    setLoading(true); setErr(null)
    try { await startTeam(team.id); onAction() }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setLoading(false) }
  }

  async function stop() {
    setLoading(true); setErr(null)
    try { await stopTeam(team.id); onAction() }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setLoading(false) }
  }

  async function del() {
    if (!confirm(`Delete team "${team.name}"? Bots will be unassigned.`)) return
    setLoading(true); setErr(null)
    try { await deleteTeam(team.id); onAction() }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Error') }
    finally { setLoading(false) }
  }

  async function scale(e: React.FormEvent) {
    e.preventDefault()
    const n = parseInt(targetCount)
    if (!n || n < 1) { setErr('Enter a valid target count'); return }
    setLoading(true); setErr(null); setScaleMsg(null)
    try {
      const r = await scaleTeam(team.id, n)
      setScaleMsg(r.msg)
      setScaleOpen(false)
      setTargetCount('')
      onAction()
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : 'Scale failed') }
    finally { setLoading(false) }
  }

  return (
    <Card style={{ padding: '14px 18px', marginBottom: 10 }}>
      {err && <Alert type="error" onClose={() => setErr(null)}>{err}</Alert>}
      {scaleMsg && <Alert type="info" onClose={() => setScaleMsg(null)}>{scaleMsg}</Alert>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Users size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text-primary)' }}>{team.name}</span>
            <StatusBadge status={team.status} />
            <Badge color="gray">{teamBots.length}/{team.spawn_cap} bots</Badge>
          </div>
          {team.description && <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{team.description}</p>}
          {team.memory_namespace && (
            <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>memory: {team.memory_namespace}</p>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          {team.status !== 'running' && (
            <button className="btn btn-primary btn-sm" disabled={loading} onClick={start} style={{ gap: 5 }}>
              <Play size={12} /> Run All
            </button>
          )}
          {team.status === 'running' && (
            <button className="btn btn-sm" disabled={loading} onClick={stop} style={{ gap: 5, background: 'var(--surface-2)' }}>
              <Square size={12} /> Stop
            </button>
          )}
          <button
            className="btn btn-sm"
            disabled={loading}
            onClick={() => setScaleOpen(!scaleOpen)}
            style={{ gap: 5, background: 'var(--surface-2)' }}
            title="Scale team (spawn bots to target count)"
          >
            <TrendingUp size={12} /> Scale
          </button>
          <button className="btn btn-ghost btn-sm" disabled={loading} onClick={del} style={{ padding: '4px 7px', color: 'var(--text-muted)' }}>
            <Trash2 size={13} />
          </button>
        </div>
      </div>
      {scaleOpen && (
        <form onSubmit={scale} style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)', display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>Scale to</span>
          <input
            className="form-input"
            type="number"
            min={1}
            max={team.spawn_cap}
            placeholder={`1–${team.spawn_cap}`}
            value={targetCount}
            onChange={e => setTargetCount(e.target.value)}
            style={{ width: 80, fontSize: 13 }}
          />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>bots (cap: {team.spawn_cap})</span>
          <button type="submit" className="btn btn-primary btn-sm" disabled={loading} style={{ gap: 4 }}>
            {loading ? <Spinner size={12} /> : <><TrendingUp size={11} /> Spawn</>}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setScaleOpen(false)}>Cancel</button>
        </form>
      )}
      {teamBots.length > 0 && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {teamBots.map(b => (
            <div key={b.id} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <Bot size={11} style={{ color: 'var(--text-muted)' }} />
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{b.name}</span>
              <StatusBadge status={b.lifecycle} />
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

// ---- Provisioning List ----
function ProvisioningList({ jobs, resources, onApprove }: {
  jobs: ProvisioningJob[]; resources: ProvisionedResource[]; onApprove: (id: number) => void
}) {
  const awaitingJobs = jobs.filter(j => j.status === 'awaiting_approval')

  if (jobs.length === 0 && resources.length === 0) {
    return <Empty message="No provisioning jobs yet." icon={<Server size={28} />} />
  }

  return (
    <div>
      {awaitingJobs.length > 0 && (
        <Alert type="warn">
          {awaitingJobs.length} provisioning job{awaitingJobs.length > 1 ? 's' : ''} awaiting your approval.
        </Alert>
      )}
      {jobs.map(job => (
        <Card key={job.id} style={{ padding: '14px 18px', marginBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
            <Package size={15} style={{ color: 'var(--accent)', flexShrink: 0, marginTop: 2 }} />
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>Job #{job.id}</span>
                <Badge color={PROVISION_COLOR[job.status] || 'gray'}>{job.status}</Badge>
                {job.bot_id && <Badge color="gray">bot #{job.bot_id}</Badge>}
                {job.team_id && <Badge color="gray">team #{job.team_id}</Badge>}
                <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>{fmtDate(job.created_at)}</span>
              </div>
              {job.plan_summary && (
                <pre style={{ background: 'var(--surface-2)', padding: '8px 10px', borderRadius: 5, fontSize: 11, overflowX: 'auto', marginBottom: 8, whiteSpace: 'pre-wrap', color: 'var(--text-secondary)', maxHeight: 140, overflow: 'auto' }}>
                  {job.plan_summary}
                </pre>
              )}
              {job.apply_summary && (
                <div style={{ fontSize: 12, color: 'var(--success)', marginBottom: 6 }}>
                  <CheckCircle size={12} style={{ marginRight: 4, display: 'inline' }} />
                  {job.apply_summary}
                </div>
              )}
              {job.error && (
                <div style={{ fontSize: 12, color: 'var(--error)', marginBottom: 6 }}>
                  <AlertCircle size={12} style={{ marginRight: 4, display: 'inline' }} />
                  {job.error}
                </div>
              )}
              {job.status === 'awaiting_approval' && (
                <button className="btn btn-primary btn-sm" onClick={() => onApprove(job.id)} style={{ gap: 5 }}>
                  <CheckCircle size={12} /> Approve &amp; Apply
                </button>
              )}
            </div>
          </div>
        </Card>
      ))}
      {resources.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Provisioned Resources</p>
          {resources.map(r => (
            <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', background: 'var(--surface-2)', borderRadius: 6, marginBottom: 8 }}>
              <Server size={14} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-primary)' }}>{r.name || r.resource_type}</span>
                  <Badge color={PROVISION_COLOR[r.status] || 'gray'}>{r.status}</Badge>
                  <Badge color="gray">{r.resource_type}</Badge>
                  <Badge color="gray">{r.provider}</Badge>
                </div>
                {r.resource_id && <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>ID: {r.resource_id}</p>}
              </div>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{fmtDate(r.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---- Status Banner ----
function StatusBanner({ status }: { status: ReturnType<typeof useFleetStatus>['data'] }) {
  if (!status) return null
  const stats = [
    { label: 'Bots', value: status.total_bots, icon: <Bot size={14} /> },
    { label: 'Teams', value: status.total_teams, icon: <Users size={14} /> },
    { label: 'Resources', value: status.total_resources, icon: <Server size={14} /> },
    { label: 'Awaiting Approval', value: status.jobs_awaiting_approval, icon: <Clock size={14} />, alert: status.jobs_awaiting_approval > 0 },
    { label: 'Running', value: (status.bot_lifecycle?.running || 0) + (status.team_status?.running || 0), icon: <Zap size={14} /> },
  ]
  return (
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 20 }}>
      {stats.map(s => (
        <div key={s.label} style={{
          background: 'var(--surface-2)', borderRadius: 8, padding: '10px 16px',
          display: 'flex', alignItems: 'center', gap: 8, minWidth: 110,
          border: s.alert ? '1px solid var(--warning)' : '1px solid var(--border)',
        }}>
          <span style={{ color: s.alert ? 'var(--warning)' : 'var(--accent)' }}>{s.icon}</span>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)' }}>{s.value}</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{s.label}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

function useFleetStatus() {
  return useApi(getFleetStatus, [])
}

// ---- Main Fleet Page ----
export function Fleet() {
  const [tab, setTab] = useState('Bots')
  const { data: statusData, refetch: refetchStatus } = useFleetStatus()
  const { data: botsData, loading: botsLoading, error: botsError, refetch: refetchBots } = useApi(listBots, [])
  const { data: teamsData, loading: teamsLoading, error: teamsError, refetch: refetchTeams } = useApi(listTeams, [])
  const { data: provData, loading: provLoading, error: provError, refetch: refetchProv } = useApi(listProvisioning, [])
  const [actionErr, setActionErr] = useState<string | null>(null)
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  function refetchAll() {
    refetchBots(); refetchTeams(); refetchProv(); refetchStatus()
  }

  async function handleApprove(jobId: number) {
    setActionErr(null); setActionMsg(null)
    try {
      await approveProvision(jobId)
      setActionMsg(`Job #${jobId} applied successfully.`)
      refetchAll()
    } catch (e: unknown) { setActionErr(e instanceof Error ? e.message : 'Apply failed') }
  }

  const bots = botsData?.bots || []
  const teams = teamsData?.teams || []
  const jobs = provData?.jobs || []
  const resources = provData?.resources || []

  return (
    <div>
      <PageHeader
        title="Bot Fleet"
        subtitle="Create, deploy, and govern persistent bots and teams"
      />

      {actionMsg && <Alert type="success" onClose={() => setActionMsg(null)}>{actionMsg}</Alert>}
      {actionErr && <Alert type="error" onClose={() => setActionErr(null)}>{actionErr}</Alert>}

      <StatusBanner status={statusData} />

      <Tabs tabs={['Bots', 'Teams', 'Provisioning']} active={tab} onChange={setTab} />

      {tab === 'Bots' && (
        <div>
          <BotBuilder teams={teams} onCreated={refetchAll} />
          {botsLoading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
          {botsError && <Alert type="error">{botsError}</Alert>}
          {!botsLoading && bots.length === 0 && (
            <Empty message="No bots defined yet. Create your first bot above." icon={<Bot size={32} />} />
          )}
          {bots.map(b => <BotCard key={b.id} bot={b} onAction={refetchAll} />)}
        </div>
      )}

      {tab === 'Teams' && (
        <div>
          <TeamBuilder onCreated={refetchAll} />
          {teamsLoading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
          {teamsError && <Alert type="error">{teamsError}</Alert>}
          {!teamsLoading && teams.length === 0 && (
            <Empty message="No teams yet. Create a team to group bots for collaborative runs." icon={<Users size={32} />} />
          )}
          {teams.map(t => <TeamCard key={t.id} team={t} bots={bots} onAction={refetchAll} />)}
        </div>
      )}

      {tab === 'Provisioning' && (
        <div>
          <ProvisionPanel onDone={refetchAll} />
          {provLoading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
          {provError && <Alert type="error">{provError}</Alert>}
          <ProvisioningList jobs={jobs} resources={resources} onApprove={handleApprove} />
        </div>
      )}
    </div>
  )
}
