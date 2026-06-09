import { type ButtonHTMLAttributes, type ReactNode, forwardRef } from 'react'
import { cn } from '@/lib/utils'

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'default' | 'ghost' | 'outline' | 'secondary' | 'destructive'
  size?: 'default' | 'sm' | 'lg' | 'icon' | 'icon-sm'
  asChild?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'default', size = 'default', children, ...props }, ref) => {
    const base = 'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-all duration-150 disabled:opacity-45 disabled:cursor-not-allowed border-none cursor-pointer'
    const variants: Record<string, string> = {
      default: 'bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)]',
      ghost: 'bg-transparent text-[var(--text-secondary)] hover:bg-white/5 hover:text-[var(--text-primary)]',
      outline: 'bg-transparent text-[var(--text-primary)] border border-[var(--border)] hover:bg-white/5',
      secondary: 'bg-white/6 text-[var(--text-primary)] border border-[var(--border)] hover:bg-white/10',
      destructive: 'bg-red-500/12 text-red-400 border border-red-500/25 hover:bg-red-500/22',
    }
    const sizes: Record<string, string> = {
      default: 'px-4 py-2 text-sm',
      sm: 'px-3 py-1.5 text-xs',
      lg: 'px-5 py-2.5 text-base',
      icon: 'p-2 w-9 h-9',
      'icon-sm': 'p-1.5 w-7 h-7',
    }
    return (
      <button
        ref={ref}
        className={cn(base, variants[variant] ?? variants.default, sizes[size] ?? sizes.default, className)}
        {...props}
      >
        {children}
      </button>
    )
  }
)
Button.displayName = 'Button'
