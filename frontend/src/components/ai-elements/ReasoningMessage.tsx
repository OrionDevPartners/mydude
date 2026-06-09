import { type ReactNode, type HTMLAttributes } from 'react'
import { Brain, ChevronDown } from 'lucide-react'
import { MessageContent } from './message'
import { cn } from '@/lib/utils'

export type ReasoningMessageProps = HTMLAttributes<HTMLDetailsElement> & {
  title?: string
  defaultOpen?: boolean
}

export function ReasoningMessage({
  children, title = 'Reasoning trace', defaultOpen = false, className, ...props
}: ReasoningMessageProps) {
  return (
    <details
      className={cn('rounded-lg border border-[var(--border)] overflow-hidden my-1', className)}
      open={defaultOpen}
      {...props}
    >
      <summary className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors list-none [&::-webkit-details-marker]:hidden">
        <Brain size={12} className="text-[var(--accent)] opacity-80 shrink-0" />
        <span className="font-medium">{title}</span>
        <ChevronDown size={12} className="ml-auto transition-transform details-open:rotate-180" />
      </summary>
      <MessageContent className="text-xs rounded-none border-t border-[var(--border)] bg-white/2 text-[var(--text-muted)]">
        {children}
      </MessageContent>
    </details>
  )
}
