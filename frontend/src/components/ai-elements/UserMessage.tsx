import { User } from 'lucide-react'
import { type HTMLAttributes } from 'react'
import { Message, MessageContent } from './message'
import { cn } from '@/lib/utils'

export type UserMessageProps = HTMLAttributes<HTMLDivElement> & {
  timestamp?: string
}

export function UserMessage({ children, timestamp, className, ...props }: UserMessageProps) {
  return (
    <Message from="user" className={cn('items-end', className)} {...props}>
      <div className="flex items-center gap-2 mb-1 justify-end">
        <span className="text-xs text-[var(--text-muted)] font-medium">You</span>
        {timestamp && <span className="text-xs text-[var(--text-muted)]">{timestamp}</span>}
        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-[var(--accent)]/15 text-[var(--accent)]">
          <User size={12} />
        </span>
      </div>
      <MessageContent>{children}</MessageContent>
    </Message>
  )
}
