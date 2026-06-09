import { Bot } from 'lucide-react'

interface ThinkingIndicatorProps {
  message?: string
}

export function ThinkingIndicator({ message = 'Multi-provider swarm executing…' }: ThinkingIndicatorProps) {
  return (
    <div className="ai-thinking">
      <div className="ai-avatar" aria-label="AI thinking">
        <Bot size={14} />
      </div>
      <div className="ai-thinking-inner">
        <div className="ai-thinking-dots" aria-label="Loading">
          <span /><span /><span />
        </div>
        <span className="ai-thinking-text">{message}</span>
      </div>
    </div>
  )
}
