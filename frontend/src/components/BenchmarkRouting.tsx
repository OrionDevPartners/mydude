import type { CSSProperties } from 'react'
import { Target, Sparkles, CheckCircle } from 'lucide-react'
import type { BenchmarkRouting } from '@/lib/api'

// Human-readable labels for the benchmark categories the swarm classifies into
// (src/swarm/benchmark_routing.py). Unknown categories degrade to a humanized form.
const CATEGORY_LABELS: Record<string, string> = {
  coding: 'Coding',
  agentic: 'Agentic',
  reasoning: 'Reasoning',
  math: 'Math',
  long_context: 'Long Context',
  creative: 'Creative',
  multilingual: 'Multilingual',
  security: 'Security',
  frontend_uiux: 'Frontend UI/UX',
  general: 'General',
}

export function categoryLabel(c: string): string {
  return CATEGORY_LABELS[c] ?? c.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
}

export function providerLabel(p: string): string {
  return p.charAt(0).toUpperCase() + p.slice(1)
}

// Compact strip that surfaces which benchmark category the prompt routed to and
// which model led the governed debate for it — deliverable #3 (benchmark-aware
// lead routing made visible). Shared by the Dashboard result panel and the full
// task report. Renders nothing until a category is known.
export function BenchmarkRoutingStrip({ routing }: { routing: BenchmarkRouting }) {
  if (!routing.category) return null
  const chip: CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px',
    fontSize: 12, borderRadius: 999, background: 'var(--accent-dim)',
    border: '1px solid var(--border-accent)', color: 'var(--text-primary)',
  }
  return (
    <div
      style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, margin: '2px 0 4px' }}
      title={routing.classification_signal ? `Classified by ${routing.classification_signal}` : undefined}
    >
      <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>
        Routing
      </span>
      <span style={chip}>
        <Target size={13} style={{ color: 'var(--accent)' }} />
        {categoryLabel(routing.category)}
      </span>
      {routing.lead_provider && (
        <span style={chip}>
          <Sparkles size={13} style={{ color: 'var(--accent)' }} />
          Lead: {providerLabel(routing.lead_provider)}
          {routing.lead_specialty && (
            <span style={{ color: 'var(--text-muted)' }}>· {routing.lead_specialty}</span>
          )}
        </span>
      )}
      {routing.bias_applied && (
        <span
          style={{ ...chip, background: 'transparent', color: 'var(--text-muted)' }}
          title="A capped, governance-gated weighting nudge was applied in the lead's favor (compliance ≥ floor, hallucination risk below threshold)."
        >
          <CheckCircle size={12} style={{ color: '#34d399' }} />
          Lead bias applied
        </span>
      )}
    </div>
  )
}
