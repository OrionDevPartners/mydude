import { type HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export type ButtonGroupProps = HTMLAttributes<HTMLDivElement> & {
  orientation?: 'horizontal' | 'vertical'
}

export function ButtonGroup({ className, orientation = 'horizontal', ...props }: ButtonGroupProps) {
  return (
    <div
      className={cn(
        'inline-flex',
        orientation === 'horizontal' ? 'flex-row' : 'flex-col',
        '[&>*:not(:first-child)]:rounded-l-none [&>*:not(:last-child)]:rounded-r-none',
        className
      )}
      {...props}
    />
  )
}

export type ButtonGroupTextProps = HTMLAttributes<HTMLSpanElement>

export function ButtonGroupText({ className, ...props }: ButtonGroupTextProps) {
  return (
    <span
      className={cn('inline-flex items-center px-2.5 text-xs text-[var(--text-muted)]', className)}
      {...props}
    />
  )
}
