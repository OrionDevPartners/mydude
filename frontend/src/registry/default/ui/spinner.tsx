import { type HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Spinner({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn('inline-block w-4 h-4 border-2 border-white/20 border-t-[var(--accent)] rounded-full animate-spin', className)}
      aria-label="Loading"
      {...props}
    />
  )
}
