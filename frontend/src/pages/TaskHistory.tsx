import { useState } from 'react'
import { Link } from 'react-router-dom'
import { getTaskHistory } from '@/lib/api'
import { useApi } from '@/hooks/useApi'
import { Card, Spinner, Alert, PageHeader, Empty } from '@/components/ui'
import { GlassStatCard } from '@/components/glass'
import { fmtDate, fmtMs, statusBadge, truncate } from '@/lib/utils'
import { Clock, ChevronRight, ChevronLeft, History, CheckCircle, XCircle, Zap } from 'lucide-react'
import type { Task } from '@/lib/api'

function taskDomain(task: Task): string | null {
  const jur = task.scores?.jurisdiction
  if (jur && typeof jur === 'object' && 'domain' in jur) {
    const d = (jur as Record<string, unknown>).domain
    return d ? String(d) : null
  }
  return null
}

function isZeroToken(task: Task): boolean {
  return task.structural_routing?.dispatched === true
}

export function TaskHistory() {
  const [page, setPage] = useState(1)
  const [zeroTokenOnly, setZeroTokenOnly] = useState(false)
  const { data, loading, error } = useApi(() => getTaskHistory(page), [page])

  const completed = data?.tasks.filter(t => t.status === 'completed').length ?? 0
  const failed = data?.tasks.filter(t => t.status === 'failed' || t.status === 'error').length ?? 0
  const zeroTokenCount = data?.tasks.filter(isZeroToken).length ?? 0
  const visibleTasks = (data?.tasks ?? []).filter(t => !zeroTokenOnly || isZeroToken(t))

  return (
    <div className="animate-fade-in">
      <PageHeader title="Task History" subtitle="All AI task runs" />

      {data && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 16 }}>
            <GlassStatCard value={data.total_pages > 1 ? `${data.tasks.length}+` : data.tasks.length} label="Tasks (page)" icon={<History size={16} />} />
            <GlassStatCard value={completed} label="Completed" icon={<CheckCircle size={16} />} glow={completed > 0} />
            <GlassStatCard value={failed} label="Failed" icon={<XCircle size={16} />} />
            <GlassStatCard value={zeroTokenCount} label="Zero-token (page)" icon={<Zap size={16} />} glow={zeroTokenCount > 0} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18 }}>
            <button
              className={`btn btn-sm ${zeroTokenOnly ? 'btn-secondary' : 'btn-ghost'}`}
              onClick={() => setZeroTokenOnly(v => !v)}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
            >
              <Zap size={13} /> {zeroTokenOnly ? 'Showing zero-token only' : 'Filter zero-token'}
            </button>
            {zeroTokenOnly && (
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{visibleTasks.length} on this page</span>
            )}
          </div>
        </>
      )}

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>}
      {error && <Alert type="error">{error}</Alert>}

      {data && (
        <>
          {visibleTasks.length === 0 ? (
            <Empty message={zeroTokenOnly ? 'No zero-token runs on this page.' : 'No tasks yet. Run your first task from the dashboard.'} icon={<History size={32} />} />
          ) : (
            <div className="glass-card" style={{ overflow: 'hidden' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Prompt</th>
                    <th>Domain</th>
                    <th>Status</th>
                    <th>Duration</th>
                    <th>Created</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {visibleTasks.map(task => (
                    <tr key={task.id}>
                      <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>#{task.id}</td>
                      <td style={{ maxWidth: 320 }}>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <span style={{ fontSize: 13.5 }}>{truncate(task.prompt, 70)}</span>
                          {isZeroToken(task) && (
                            <span className="badge badge-green" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                              <Zap size={11} /> zero-token
                            </span>
                          )}
                        </span>
                      </td>
                      <td>
                        {taskDomain(task)
                          ? <span className="badge badge-gray">{taskDomain(task)}</span>
                          : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>—</span>}
                      </td>
                      <td><span className={`badge ${statusBadge(task.status)}`}>{task.status}</span></td>
                      <td style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{fmtMs(task.execution_time_ms)}</td>
                      <td style={{ color: 'var(--text-muted)', fontSize: 12, whiteSpace: 'nowrap' }}>
                        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <Clock size={11} /> {fmtDate(task.created_at)}
                        </span>
                      </td>
                      <td>
                        <Link to={`/tasks/${task.id}`} className="btn btn-ghost btn-sm">
                          View <ChevronRight size={13} />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data.total_pages > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12, marginTop: 20 }}>
              <button className="btn btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
                <ChevronLeft size={14} /> Prev
              </button>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                Page {data.page} of {data.total_pages}
              </span>
              <button className="btn btn-secondary btn-sm" disabled={page >= data.total_pages} onClick={() => setPage(p => p + 1)}>
                Next <ChevronRight size={14} />
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
