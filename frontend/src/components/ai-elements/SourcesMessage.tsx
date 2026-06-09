import { Link2 } from 'lucide-react'

interface Source {
  label: string
  url?: string
}

interface SourcesMessageProps {
  sources: (string | Source)[]
  title?: string
}

function toSource(s: string | Source): Source {
  return typeof s === 'string' ? { label: s } : s
}

export function SourcesMessage({ sources, title = 'Sources' }: SourcesMessageProps) {
  if (!sources.length) return null
  return (
    <div className="ai-sources">
      <p className="ai-sources-title">
        <Link2 size={11} />
        {title}
      </p>
      <div className="ai-sources-list">
        {sources.map((s, i) => {
          const src = toSource(s)
          return src.url ? (
            <a key={i} href={src.url} target="_blank" rel="noopener noreferrer" className="ai-source-chip">
              {src.label}
            </a>
          ) : (
            <span key={i} className="ai-source-chip">{src.label}</span>
          )
        })}
      </div>
    </div>
  )
}
