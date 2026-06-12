import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { getDashboard, runTask, getTask, type Task } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader } from '@/components/ui'
import { fmtDate, fmtMs, statusBadge, truncate } from '@/lib/utils'
import {
  PromptInput, PromptInputBody, PromptInputTextarea,
  PromptInputActions, PromptInputActionSend,
  UserMessage, AssistantMessage, ReasoningMessage,
  SourcesMessage, CodeBlock, ThinkingIndicator, ScoreBar, MessageThread,
} from '@/components/ai-elements'
import { ChevronRight, Clock, Key, MapPin } from 'lucide-react'

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

  const fallbackText = task.result ?? '(no output)'

  const ts = fmtMs(task.execution_time_ms ?? 0)
  const statusEl = (
    <span className={`badge ${statusBadge(task.status)}`}>{task.status}</span>
  )

  return (
    <MessageThread>
      {/* User prompt bubble */}
      <UserMessage>{prompt}</UserMessage>

      {/* Scores strip */}
      {scores && (
        <div className="ai-scores-strip">
          {scores.hallucination_risk != null && typeof scores.hallucination_risk === 'number' && (
            <div style={{ minWidth: 200 }}>
              <ScoreBar
                label="Hallucination Risk"
                value={scores.hallucination_risk as number}
                colorFn={riskColor}
              />
            </div>
          )}
          {scores.compliance != null && typeof scores.compliance === 'number' && (
            <div style={{ minWidth: 200 }}>
              <ScoreBar
                label="Compliance Score"
                value={scores.compliance as number}
                colorFn={v => v > 0.65 ? '#34d399' : v > 0.35 ? '#fbbf24' : '#f87171'}
              />
            </div>
          )}
        </div>
      )}

      {/* Main AI response */}
      <AssistantMessage timestamp={ts} badge={statusEl}>
        <div style={{ lineHeight: 1.75 }}>
          {mainText ?? fallbackText}
        </div>

        {/* Code block if present */}
        {codeBlocks && typeof codeBlocks === 'string' && (
          <div style={{ marginTop: 14 }}>
            <CodeBlock code={codeBlocks} />
          </div>
        )}

        {/* Sources */}
        {sources && Array.isArray(sources) && sources.length > 0 && (
          <SourcesMessage sources={sources as string[]} />
        )}
      </AssistantMessage>

      {/* Reasoning trace */}
      {reasoning && (
        <ReasoningMessage>
          {typeof reasoning === 'object' ? JSON.stringify(reasoning, null, 2) : String(reasoning)}
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
      // Poll for completion
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

  return (
    <div>
      <PageHeader title="AI Task Runner" subtitle="Ask MyDude to automate your business workflows" />

      {!data?.has_keys && (
        <div className="alert alert-warn" style={{ marginBottom: 20 }}>
          <Key size={15} />
          <span>No API keys configured. <Link to="/keys" style={{ color: 'var(--accent)', fontWeight: 600 }}>Add keys →</Link> to enable AI tasks.</span>
        </div>
      )}

      {/* Prompt card */}
      <Card style={{ marginBottom: 24 }}>
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

        {/* Thinking state */}
        {running && !currentTask && (
          <div style={{ padding: '4px 20px 18px', borderTop: '1px solid var(--border)' }}>
            <ThinkingIndicator />
          </div>
        )}

        {/* Result */}
        {(currentTask || (running && submittedPrompt)) && (
          <div style={{ padding: '4px 20px 20px', borderTop: '1px solid var(--border)' }}>
            {currentTask
              ? <ResultPanel task={currentTask} prompt={submittedPrompt} />
              : running && <ThinkingIndicator />}
          </div>
        )}
      </Card>

      {/* Recent tasks */}
      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}
      {data && data.recent_tasks.length > 0 && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
            <h2 style={{ fontSize: 15, fontWeight: 700 }}>Recent tasks</h2>
            <Link to="/history" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 4 }}>
              View all <ChevronRight size={13} />
            </Link>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.recent_tasks.map(task => (
              <Link key={task.id} to={`/tasks/${task.id}`} style={{ textDecoration: 'none' }}>
                <Card style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 14, cursor: 'pointer' }}>
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
        </div>
      )}
    </div>
  )
}
