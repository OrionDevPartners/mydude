import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { getDashboard, runTask, getTask, type Task } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader } from '@/components/ui'
import { GlassStatCard, GlassSection } from '@/components/glass'
import { fmtDate, fmtMs, statusBadge, truncate } from '@/lib/utils'
import {
  PromptInput, PromptInputBody, PromptInputTextarea,
  PromptInputActions, PromptInputActionSend,
  UserMessage, AssistantMessage, ReasoningMessage,
  SourcesMessage, CodeBlock, ThinkingIndicator, ScoreBar, MessageThread,
} from '@/components/ai-elements'
import { ChevronRight, Clock, Key, MapPin, Zap, CheckCircle, History } from 'lucide-react'

function riskColor(v: number): string {
  if (v < 0.35) return '#34d399'
  if (v < 0.65) return '#fbbf24'
  return '#f87171'
}

function ResultPanel({ task, prompt }: { task: Task; prompt: string }) {
  const parsed = task.parsed
  const scores = task.scores

  const mainKeys = ['SYNTHESIS', 'SUMMARY', 'RESULT', 'OUTPUT', 'ANSWER', 'RESPONSE']
  const mainKey = mainKeys.find(k => parsed?.[k])
  const mainText = mainKey ? String(parsed![mainKey]) : null

  const reasoning = parsed?.['REASONING'] || parsed?.['reasoning'] || parsed?.['DEBATE_SUMMARY']
  const sources = parsed?.['SOURCES'] || parsed?.['sources']
  const codeBlocks = parsed?.['CODE'] || parsed?.['code'] || parsed?.['CODE_BLOCK']

  const dissent = Array.isArray(parsed?.['DISSENT_LOG'])
    ? (parsed!['DISSENT_LOG'] as unknown[]).map(d => (typeof d === 'string' ? d : JSON.stringify(d))).filter(d => d.trim())
    : []
  const ledgerRaw = parsed?.['CLAIM_LEDGER']
  const ledger = typeof ledgerRaw === 'string' && ledgerRaw.trim() && ledgerRaw.trim().toLowerCase() !== 'no claims recorded'
    ? ledgerRaw.trim()
    : ''
  const hasDebate = dissent.length > 0 || ledger !== ''

  const fallbackText = task.result ?? '(no output)'

  const ts = fmtMs(task.execution_time_ms ?? 0)
  const statusEl = (
    <span className={`badge ${statusBadge(task.status)}`}>{task.status}</span>
  )

  return (
    <MessageThread>
      <UserMessage>{prompt}</UserMessage>

      {scores && (
        <div className="ai-scores-strip">
          {scores.hallucination_risk != null && typeof scores.hallucination_risk === 'number' && (
            <div style={{ minWidth: 200 }}>
              <ScoreBar label="Hallucination Risk" value={scores.hallucination_risk as number} colorFn={riskColor} />
            </div>
          )}
          {scores.compliance != null && typeof scores.compliance === 'number' && (
            <div style={{ minWidth: 200 }}>
              <ScoreBar label="Compliance Score" value={scores.compliance as number} colorFn={v => v > 0.65 ? '#34d399' : v > 0.35 ? '#fbbf24' : '#f87171'} />
            </div>
          )}
        </div>
      )}

      <AssistantMessage timestamp={ts} badge={statusEl}>
        <div style={{ lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>
          {mainText ?? fallbackText}
        </div>
        {codeBlocks && typeof codeBlocks === 'string' && (
          <div style={{ marginTop: 14 }}>
            <CodeBlock code={codeBlocks} />
          </div>
        )}
        {sources && Array.isArray(sources) && sources.length > 0 && (
          <SourcesMessage sources={sources as string[]} />
        )}
      </AssistantMessage>

      {reasoning && (
        <ReasoningMessage>
          {typeof reasoning === 'object' ? JSON.stringify(reasoning, null, 2) : String(reasoning)}
        </ReasoningMessage>
      )}

      {hasDebate && (
        <ReasoningMessage title="Reasoning & debate">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {ledger && (
              <div>
                <p style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: 6 }}>
                  Claim ledger
                </p>
                <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{ledger}</div>
              </div>
            )}
            {dissent.length > 0 && (
              <div>
                <p style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: 6 }}>
                  Debate &amp; dissent ({dissent.length})
                </p>
                <ul style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 5, lineHeight: 1.55 }}>
                  {dissent.slice(0, 12).map((d, i) => (
                    <li key={i} style={{ whiteSpace: 'pre-wrap' }}>{d}</li>
                  ))}
                </ul>
                {dissent.length > 12 && (
                  <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
                    +{dissent.length - 12} more in the full report
                  </p>
                )}
              </div>
            )}
          </div>
        </ReasoningMessage>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 4 }}>
        <Link to={`/tasks/${task.id}`} style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 4 }}>
          View full report <ChevronRight size={13} />
        </Link>
      </div>
    </MessageThread>
  )
}

function domainLabel(d: string): string {
  return d.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
}

export function Dashboard() {
  const { data, loading, error, refetch } = useApi(getDashboard, [])
  const [prompt, setPrompt] = useState('')
  const [domain, setDomain] = useState('general')
  const [submittedPrompt, setSubmittedPrompt] = useState('')
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [currentTask, setCurrentTask] = useState<Task | null>(null)

  const domains = data?.domains?.length ? data.domains : ['general']

  async function handleRun(e: FormEvent) {
    e.preventDefault()
    if (!prompt.trim() || running) return
    const p = prompt.trim()
    setRunning(true)
    setRunError(null)
    setCurrentTask(null)
    setSubmittedPrompt(p)

    try {
      const { task_id } = await runTask(p, domain)
      const iv = setInterval(async () => {
        try {
          const task = await getTask(task_id)
          setCurrentTask(task)
          if (task.status !== 'running') {
            clearInterval(iv)
            setRunning(false)
            setPrompt('')
            refetch()
          }
        } catch {
          clearInterval(iv)
          setRunning(false)
        }
      }, 1500)
    } catch (err: unknown) {
      setRunError(err instanceof Error ? err.message : 'Failed to run task')
      setRunning(false)
    }
  }

  const completedTasks = data?.recent_tasks?.filter(t => t.status === 'completed').length ?? 0

  return (
    <div className="animate-fade-in">
      <PageHeader title="AI Task Runner" subtitle="Ask MyDude to automate your business workflows" />

      {!data?.has_keys && (
        <div className="alert alert-warn" style={{ marginBottom: 20 }}>
          <Key size={15} />
          <span>No API keys configured. <Link to="/keys" style={{ color: 'var(--accent)', fontWeight: 600 }}>Add keys →</Link> to enable AI tasks.</span>
        </div>
      )}

      {data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 24 }}>
          <GlassStatCard
            value={data.recent_tasks.length}
            label="Recent tasks"
            icon={<History size={16} />}
          />
          <GlassStatCard
            value={completedTasks}
            label="Completed"
            icon={<CheckCircle size={16} />}
            glow={completedTasks > 0}
          />
          <GlassStatCard
            value={data.has_keys ? 'Active' : 'None'}
            label="API keys"
            icon={<Key size={16} />}
            glow={data.has_keys}
          />
          <GlassStatCard
            value={domains.length}
            label="Domains"
            icon={<MapPin size={16} />}
          />
        </div>
      )}

      <GlassSection title="Run a Task" className="animate-fade-in-up">
        <div className="glass-card-glow" style={{ borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
          <div style={{ padding: '16px 20px 14px' }}>
            <PromptInput
              value={prompt}
              onChange={setPrompt}
              onSubmit={handleRun}
              status={running ? 'submitted' : 'ready'}
            >
              <PromptInputBody>
                <PromptInputTextarea
                  placeholder="Ask MyDude to draft a proposal, research a topic, analyze data, automate a workflow…"
                />
              </PromptInputBody>
              <PromptInputActions>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-muted)' }}>
                  <MapPin size={13} />
                  <select
                    className="form-input"
                    value={domain}
                    onChange={e => setDomain(e.target.value)}
                    disabled={running}
                    title="Route this request to a jurisdiction domain"
                    style={{ padding: '4px 8px', fontSize: 12, height: 'auto', width: 'auto' }}
                  >
                    {domains.map(d => (
                      <option key={d} value={d}>{domainLabel(d)}</option>
                    ))}
                  </select>
                </label>
                <PromptInputActionSend />
              </PromptInputActions>
            </PromptInput>
          </div>

          {runError && (
            <div style={{ padding: '0 20px 16px' }}>
              <Alert type="error">{runError}</Alert>
            </div>
          )}

          {running && !currentTask && (
            <div style={{ padding: '4px 20px 18px', borderTop: '1px solid var(--border)' }}>
              <ThinkingIndicator />
            </div>
          )}

          {(currentTask || (running && submittedPrompt)) && (
            <div style={{ padding: '4px 20px 20px', borderTop: '1px solid var(--border)' }}>
              {currentTask
                ? <ResultPanel task={currentTask} prompt={submittedPrompt} />
                : running && <ThinkingIndicator />}
            </div>
          )}
        </div>
      </GlassSection>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && data.recent_tasks.length > 0 && (
        <GlassSection
          title="Recent Tasks"
          actions={
            <Link to="/history" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 4 }}>
              View all <ChevronRight size={13} />
            </Link>
          }
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.recent_tasks.map((task, i) => (
              <Link key={task.id} to={`/tasks/${task.id}`} style={{ textDecoration: 'none' }} className="animate-fade-in-up" style={{ animationDelay: `${i * 40}ms` }}>
                <Card style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 14, cursor: 'pointer' }}>
                  <div style={{
                    width: 32, height: 32, borderRadius: 8, flexShrink: 0,
                    background: 'var(--accent-dim)', border: '1px solid var(--border-accent)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    <Zap size={14} style={{ color: 'var(--accent)' }} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <p style={{ fontSize: 13.5, color: 'var(--text-primary)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {truncate(task.prompt, 80)}
                    </p>
                    <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2, display: 'flex', alignItems: 'center', gap: 5 }}>
                      <Clock size={11} /> {fmtDate(task.created_at)}
                      {task.execution_time_ms != null && <span>· {fmtMs(task.execution_time_ms)}</span>}
                    </p>
                  </div>
                  <span className={`badge ${statusBadge(task.status)}`}>{task.status}</span>
                  <ChevronRight size={15} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
                </Card>
              </Link>
            ))}
          </div>
        </GlassSection>
      )}
    </div>
  )
}
