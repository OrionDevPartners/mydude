import { type HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export type MessageThreadProps = HTMLAttributes<HTMLDivElement>

export function MessageThread({ children, className, ...props }: MessageThreadProps) {
  return (
    <div
      role="log"
      aria-live="polite"
      aria-label="AI conversation"
      className={cn('flex flex-col gap-4 w-full max-w-3xl mx-auto py-4', className)}
      {...props}
    >
      {children}
    </div>
  )
}
