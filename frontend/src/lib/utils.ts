export function cn(...classes: (string | undefined | null | false)[]): string {
  return classes.filter(Boolean).join(' ')
}

export function fmtDate(d: string | null | undefined): string {
  if (!d) return '—'
  return new Date(d).toLocaleString()
}

export function fmtDateShort(d: string | null | undefined): string {
  if (!d) return '—'
  return new Date(d).toLocaleDateString()
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function statusBadge(status: string): string {
  const map: Record<string, string> = {
    completed: 'badge-green', running: 'badge-yellow', failed: 'badge-red',
    ok: 'badge-green', error: 'badge-red', degraded: 'badge-yellow',
    confirmed: 'badge-green', candidate: 'badge-blue', dismissed: 'badge-gray',
    cancelled: 'badge-gray', cancel_pending: 'badge-yellow',
    enabled: 'badge-green', disabled: 'badge-gray',
  }
  return map[status] || 'badge-gray'
}

export function severityBadge(sev: string): string {
  const map: Record<string, string> = { high: 'badge-red', medium: 'badge-yellow', low: 'badge-blue', info: 'badge-gray' }
  return map[sev?.toLowerCase()] || 'badge-gray'
}

export function truncate(s: string, n: number): string {
  if (s.length <= n) return s
  return s.slice(0, n) + '…'
}
