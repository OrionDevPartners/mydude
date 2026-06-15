import { ReactNode, useState } from 'react'
import { cn } from '@/lib/utils'
import { ChevronDown, ChevronUp, AlertCircle, CheckCircle, Info, AlertTriangle } from 'lucide-react'

// ----- Alert -----
interface AlertProps { type?: 'success' | 'error' | 'warn' | 'info'; children: ReactNode; onClose?: () => void }
export function Alert({ type = 'info', children, onClose }: AlertProps) {
  const cls = { success: 'alert-success', error: 'alert-error', warn: 'alert-warn', info: 'alert-info' }[type]
  const Icon = { success: CheckCircle, error: AlertCircle, warn: AlertTriangle, info: Info }[type]
  return (
    <div className={`alert ${cls}`} style={{ marginBottom: 16 }}>
      <Icon size={16} style={{ flexShrink: 0, marginTop: 1 }} />
      <span style={{ flex: 1 }}>{children}</span>
      {onClose && <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', padding: 0, lineHeight: 1 }}>×</button>}
    </div>
  )
}

// ----- Badge -----
export function Badge({ children, color = 'gray', style }: { children: ReactNode; color?: string; style?: React.CSSProperties }) {
  const cls = `badge badge-${color}`
  return <span className={cls} style={style}>{children}</span>
}

// ----- Card -----
export function Card({ children, className, style, onClick }: { children: ReactNode; className?: string; style?: React.CSSProperties; onClick?: React.MouseEventHandler<HTMLDivElement> }) {
  return <div className={cn('glass-card', className)} style={style} onClick={onClick}>{children}</div>
}

// ----- Spinner -----
export function Spinner({ size = 18 }: { size?: number }) {
  return <div className="spinner" style={{ width: size, height: size }} />
}

// ----- Empty state -----
export function Empty({ message = 'No data', icon }: { message?: string; icon?: ReactNode }) {
  return (
    <div style={{ textAlign: 'center', padding: '48px 20px', color: 'var(--text-muted)' }}>
      {icon && <div style={{ marginBottom: 12, opacity: 0.5 }}>{icon}</div>}
      <p style={{ fontSize: 14 }}>{message}</p>
    </div>
  )
}

// ----- Toggle -----
export function Toggle({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="toggle" style={{ opacity: disabled ? 0.5 : 1 }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} disabled={disabled} />
      <span className="toggle-slider" />
    </label>
  )
}

// ----- Collapsible -----
export function Collapsible({ title, children, defaultOpen = false }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div style={{ borderBottom: '1px solid var(--border)' }}>
      <button
        onClick={() => setOpen(!open)}
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: '14px 0', color: 'var(--text-primary)', fontSize: 14, fontWeight: 600 }}
      >
        {title}
        {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {open && <div style={{ paddingBottom: 16 }}>{children}</div>}
    </div>
  )
}

// ----- Modal -----
export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  if (!open) return null
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }} onClick={onClose}>
      <div style={{ background: '#0f1629', border: '1px solid var(--border)', borderRadius: 14, padding: 24, maxWidth: 520, width: '100%', maxHeight: '90vh', overflowY: 'auto' }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <h3 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>{title}</h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: 20 }}>×</button>
        </div>
        {children}
      </div>
    </div>
  )
}

// ----- Tabs -----
export function Tabs({ tabs, active, onChange }: { tabs: string[]; active: string; onChange: (t: string) => void }) {
  return (
    <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--border)', marginBottom: 20 }}>
      {tabs.map(t => (
        <button
          key={t}
          onClick={() => onChange(t)}
          style={{
            background: 'none', border: 'none', cursor: 'pointer', padding: '10px 16px',
            fontSize: 13.5, fontWeight: 600, color: active === t ? 'var(--accent)' : 'var(--text-secondary)',
            borderBottom: active === t ? '2px solid var(--accent)' : '2px solid transparent',
            marginBottom: -1, transition: 'color 0.15s',
          }}
        >
          {t}
        </button>
      ))}
    </div>
  )
}

// ----- PageHeader -----
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 22, gap: 12, flexWrap: 'wrap' }}>
      <div>
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>{actions}</div>}
    </div>
  )
}

// ----- Input helpers -----
export function FormField({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      {children}
      {hint && <p className="form-hint">{hint}</p>}
    </div>
  )
}

// ----- Screenshot -----
export function Screenshot({ b64 }: { b64: string }) {
  return (
    <div style={{ marginTop: 12, borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)' }}>
      <img src={`data:image/png;base64,${b64}`} alt="Screenshot" style={{ width: '100%', display: 'block' }} />
    </div>
  )
}
