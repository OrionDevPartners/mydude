import { type ReactNode, type HTMLAttributes } from 'react'
import { Bot } from 'lucide-react'
import { Message, MessageContent, MessageToolbar } from './message'
import { cn } from '@/lib/utils'

export type AssistantMessageProps = HTMLAttributes<HTMLDivElement> & {
  name?: string
  timestamp?: string
  badge?: ReactNode
}

export function AssistantMessage({
  children, name = 'MyDude AI', timestamp, badge, className, ...props
}: AssistantMessageProps) {
  return (
    <Message from="assistant" className={cn('items-start', className)} {...props}>
      <div className="flex items-center gap-2 mb-1">
        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-[var(--accent)]/10 text-[var(--accent)]">
          <Bot size={12} />
        </span>
        <span className="text-xs text-[var(--text-muted)] font-medium">{name}</span>
        {timestamp && <span className="text-xs text-[var(--text-muted)]">{timestamp}</span>}
        {badge && <MessageToolbar className="ml-auto">{badge}</MessageToolbar>}
      </div>
      <MessageContent>{children}</MessageContent>
    </Message>
  )
}
