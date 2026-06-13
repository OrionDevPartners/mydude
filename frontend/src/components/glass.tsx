import { type ReactNode, type ButtonHTMLAttributes, type InputHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/* ─────────────────────────────────────────────────────────────
   GlassPanel — full-bleed frosted glass surface (sidebar, modal backdrop)
───────────────────────────────────────────────────────────── */
export function GlassPanel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('glass-panel', className)}>
      {children}
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────
   GlassCard — standard content card
───────────────────────────────────────────────────────────── */
interface GlassCardProps {
  children: ReactNode
  className?: string
  padding?: number | string
  glow?: boolean
  style?: React.CSSProperties
}
export function GlassCard({ children, className, padding = 20, glow = false, style }: GlassCardProps) {
  return (
    <div
      className={cn(glow ? 'glass-card-glow' : 'glass-card', className)}
      style={{ padding, ...style }}
    >
      {children}
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────
   GlassButton — all button variants in one primitive
───────────────────────────────────────────────────────────── */
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize    = 'xs' | 'sm' | 'md'

const variantClass: Record<ButtonVariant, string> = {
  primary:   'btn-primary',
  secondary: 'btn-secondary',
  ghost:     'btn-ghost',
  danger:    'btn-danger',
}
const sizeClass: Record<ButtonSize, string> = {
  xs: 'btn-xs',
  sm: 'btn-sm',
  md: '',
}

interface GlassButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  children: ReactNode
}
export function GlassButton({
  variant = 'secondary', size = 'md', children, className, ...props
}: GlassButtonProps) {
  return (
    <button
      className={cn('btn', variantClass[variant], sizeClass[size], className)}
      {...props}
    >
      {children}
    </button>
  )
}

/* ─────────────────────────────────────────────────────────────
   GlassInput — styled text input
───────────────────────────────────────────────────────────── */
export function GlassInput({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn('form-input', className)} {...props} />
}

/* ─────────────────────────────────────────────────────────────
   GlassTextarea — styled textarea
───────────────────────────────────────────────────────────── */
export function GlassTextarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn('form-input', className)} {...props} />
}

/* ─────────────────────────────────────────────────────────────
   GlassSelect — styled select
───────────────────────────────────────────────────────────── */
export function GlassSelect({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn('form-input', className)} {...props} />
}

/* ─────────────────────────────────────────────────────────────
   GlassBadge — semantic badge
───────────────────────────────────────────────────────────── */
type BadgeColor = 'green' | 'red' | 'yellow' | 'blue' | 'purple' | 'gray' | 'accent'
export function GlassBadge({ children, color = 'gray' }: { children: ReactNode; color?: BadgeColor }) {
  return <span className={cn('badge', `badge-${color}`)}>{children}</span>
}

/* ─────────────────────────────────────────────────────────────
   GlassDivider — subtle horizontal rule
───────────────────────────────────────────────────────────── */
export function GlassDivider({ className }: { className?: string }) {
  return (
    <div
      className={cn(className)}
      style={{ height: 1, background: 'var(--glass-border)', margin: '16px 0' }}
    />
  )
}

/* ─────────────────────────────────────────────────────────────
   GlassSection — labelled section header inside a page
───────────────────────────────────────────────────────────── */
export function GlassSection({
  title, actions, children, className,
}: {
  title: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('mb-6', className)}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <h3 style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          {title}
        </h3>
        {actions}
      </div>
      {children}
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────
   GlassStatCard — key metric card with optional glow
───────────────────────────────────────────────────────────── */
export function GlassStatCard({
  value, label, icon, glow = false, className,
}: {
  value: ReactNode
  label: string
  icon?: ReactNode
  glow?: boolean
  className?: string
}) {
  return (
    <GlassCard glow={glow} className={cn('stat-card', className)}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div className="stat-value">{value}</div>
          <div className="stat-label">{label}</div>
        </div>
        {icon && (
          <div style={{
            width: 36, height: 36, borderRadius: 10,
            background: 'var(--accent-dim)', border: '1px solid var(--border-accent)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: 'var(--accent)',
          }}>
            {icon}
          </div>
        )}
      </div>
    </GlassCard>
  )
}
