import { useParams, Link } from 'react-router-dom'
import { getTask } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader } from '@/components/ui'
import { GlassCard, GlassStatCard, GlassDivider } from '@/components/glass'
import { fmtDate, fmtMs, statusBadge } from '@/lib/utils'
import {
  AssistantMessage, UserMessage, ReasoningMessage, SourcesMessage,
  CodeBlock, ScoreBar, MessageThread,
} from '@/components/ai-elements'
import { ArrowLeft, Clock, MapPin, ShieldCheck, Zap } from 'lucide-react'

function riskColor(v: number) {
  if (v < 0.35) return '#34d399'
  if (v < 0.65) return '#fbbf24'
  return '#f87171'
}
function complianceColor(v: number) {
  return v > 0.65 ? '#34d399' : v > 0.35 ? '#fbbf24' : '#f87171'
}

export function TaskDetail() {
  const { id } = useParams<{ id: string }>()
  const { data: task, loading, error } = useApi(() => getTask(Number(id)), [id])

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><Spinner /></div>
  if (error) return <Alert type="error">{error}</Alert>
  if (!task) return null

  const scores = task.scores || {}
  const parsed = task.parsed

  const mainKeys = ['SYNTHESIS', 'SUMMARY', 'RESULT', 'OUTPUT', 'ANSWER', 'RESPONSE']
  const skipKeys = new Set(['COMPLIANCE_SCORES', 'HALLUCINATION_RISK', 'JURISDICTION', 'WAVE_METADATA'])

  const mainKey = mainKeys.find(k => parsed?.[k])
  const mainText = mainKey ? String(parsed![mainKey]) : null
  const reasoning = parsed?.['REASONING'] || parsed?.['reasoning'] || parsed?.['DEBATE_SUMMARY']
  const sources = parsed?.['SOURCES'] || parsed?.['sources']
  const codeContent = parsed?.['CODE'] || parsed?.['code'] || parsed?.['CODE_BLOCK']

  const hasScores = scores.hallucination_risk != null || scores.compliance != null || scores.jurisdiction != null

  const jur = (scores.jurisdiction && typeof scores.jurisdiction === 'object')
    ? scores.jurisdiction as Record<string, unknown>
    : null
  const jurText = jur
    ? [
        jur.domain ? `domain: ${String(jur.domain)}` : null,
        jur.exec_locus ? `exec: ${String(jur.exec_locus)}` : null,
        jur.fallback_tier != null ? `tier ${String(jur.fallback_tier)}` : null,
      ].filter(Boolean).join(' · ')
    : (scores.jurisdiction != null ? String(scores.jurisdiction) : null)

  return (
    <div className="animate-fade-in">
      <Link to="/history" className="btn btn-ghost btn-sm" style={{ marginBottom: 14, paddingLeft: 0 }}>
        <ArrowLeft size={14} /> Back to history
      </Link>

      <PageHeader
        title={`Task #${task.id}`}
        subtitle={`Created ${fmtDate(task.created_at)}`}
        actions={<span className={`badge ${statusBadge(task.status)}`} style={{ fontSize: 13 }}>{task.status}</span>}
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginBottom: 22 }}>
        {task.execution_time_ms != null && (
          <GlassStatCard value={fmtMs(task.execution_time_ms)} label="Execution time" icon={<Clock size={16} />} />
        )}
        <GlassStatCard value={task.status} label="Status" icon={<Zap size={16} />} glow={task.status === 'completed'} />
        {jurText && (
          <GlassStatCard value={jurText} label="Jurisdiction" icon={<MapPin size={16} />} />
        )}
        {scores.compliance != null && typeof scores.compliance === 'number' && (
          <GlassStatCard
            value={`${(scores.compliance * 100).toFixed(0)}%`}
            label="Compliance"
            icon={<ShieldCheck size={16} />}
            glow={(scores.compliance as number) > 0.65}
          />
        )}
      </div>

      {hasScores && (
        <GlassCard padding="18px 20px" style={{ marginBottom: 20 }}>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 14 }}>
            Governance Scores
          </p>
          {scores.hallucination_risk != null && typeof scores.hallucination_risk === 'number' && (
            <ScoreBar label="Hallucination Risk" value={scores.hallucination_risk as number} colorFn={riskColor} />
          )}
          {scores.compliance != null && typeof scores.compliance === 'number' && (
            <ScoreBar label="Compliance Score" value={scores.compliance as number} colorFn={complianceColor} />
          )}
        </GlassCard>
      )}

      <MessageThread>
        <UserMessage>{task.prompt}</UserMessage>

        <AssistantMessage
          timestamp={task.execution_time_ms ? fmtMs(task.execution_time_ms) : undefined}
          badge={<span className={`badge ${statusBadge(task.status)}`}>{task.status}</span>}
        >
          <div style={{ lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>
            {mainText ?? (task.result ?? '(no output)')}
          </div>

          {codeContent && typeof codeContent === 'string' && (
            <div style={{ marginTop: 16 }}>
              <CodeBlock code={codeContent} />
            </div>
          )}

          {sources && Array.isArray(sources) && sources.length > 0 && (
            <SourcesMessage sources={sources as string[]} />
          )}
        </AssistantMessage>

        {reasoning && (
          <ReasoningMessage defaultOpen={false}>
            {typeof reasoning === 'object' ? JSON.stringify(reasoning, null, 2) : String(reasoning)}
          </ReasoningMessage>
        )}

        {parsed && Object.entries(parsed)
          .filter(([k]) => !mainKeys.includes(k) && !skipKeys.has(k) && k !== 'REASONING' && k !== 'reasoning' && k !== 'DEBATE_SUMMARY' && k !== 'SOURCES' && k !== 'sources' && k !== 'CODE' && k !== 'code' && k !== 'CODE_BLOCK')
          .map(([key, val]) => (
            <ReasoningMessage key={key} title={key.replace(/_/g, ' ')}>
              {typeof val === 'string' ? val : JSON.stringify(val, null, 2)}
            </ReasoningMessage>
          ))
        }
      </MessageThread>

      {task.result && (
        <details style={{ marginTop: 22 }}>
          <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--text-muted)', userSelect: 'none', fontWeight: 600 }}>
            Raw JSON output
          </summary>
          <div style={{ marginTop: 10 }}>
            <CodeBlock code={task.result} language="json" />
          </div>
        </details>
      )}
    </div>
  )
}
