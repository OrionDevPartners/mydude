import {
  type ReactNode,
  type FormEvent,
  type KeyboardEvent,
  type HTMLAttributes,
  type TextareaHTMLAttributes,
  type ButtonHTMLAttributes,
  useRef, useEffect, createContext, useContext,
} from 'react'
import { Send, Square } from 'lucide-react'
import { cn } from '@/lib/utils'

type PromptInputStatus = 'ready' | 'streaming' | 'submitted' | 'error'

interface PromptInputContextValue {
  value: string
  onChange: (v: string) => void
  onSubmit: (e: FormEvent) => void
  disabled?: boolean
  status?: PromptInputStatus
}

const PromptInputContext = createContext<PromptInputContextValue | null>(null)

function usePromptInput() {
  const ctx = useContext(PromptInputContext)
  if (!ctx) throw new Error('PromptInput sub-components must be inside <PromptInput>')
  return ctx
}

export type PromptInputProps = HTMLAttributes<HTMLFormElement> & {
  value: string
  onChange: (v: string) => void
  onSubmit: (e: FormEvent) => void
  disabled?: boolean
  status?: PromptInputStatus
}

export function PromptInput({
  value, onChange, onSubmit, disabled, status = 'ready',
  children, className, ...props
}: PromptInputProps) {
  return (
    <PromptInputContext.Provider value={{ value, onChange, onSubmit, disabled, status }}>
      <form
        onSubmit={onSubmit}
        className={cn(
          'rounded-xl border border-[var(--border)] bg-[var(--bg-glass)] backdrop-blur-sm',
          'flex flex-col gap-0 overflow-hidden shadow-lg',
          className
        )}
        {...props}
      >
        {children}
      </form>
    </PromptInputContext.Provider>
  )
}

export type PromptInputBodyProps = HTMLAttributes<HTMLDivElement>

export function PromptInputBody({ children, className, ...props }: PromptInputBodyProps) {
  return (
    <div className={cn('flex flex-col flex-1 min-h-0', className)} {...props}>
      {children}
    </div>
  )
}

export type PromptInputTextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  autoFocus?: boolean
}

export function PromptInputTextarea({ autoFocus, className, ...props }: PromptInputTextareaProps) {
  const { value, onChange, onSubmit, disabled, status } = usePromptInput()
  const ref = useRef<HTMLTextAreaElement>(null)
  const busy = status === 'streaming' || status === 'submitted'

  useEffect(() => {
    if (!busy && autoFocus !== false && ref.current) ref.current.focus()
  }, [busy, autoFocus])

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      onSubmit(e as unknown as FormEvent)
    }
  }

  return (
    <textarea
      ref={ref}
      value={value}
      onChange={ev => onChange(ev.target.value)}
      onKeyDown={handleKeyDown}
      disabled={disabled || busy}
      aria-label="Prompt"
      className={cn(
        'w-full resize-none bg-transparent px-4 pt-4 pb-2 text-sm text-[var(--text-primary)]',
        'placeholder:text-[var(--text-muted)] outline-none border-none min-h-[5rem]',
        className
      )}
      {...props}
    />
  )
}

export type PromptInputActionsProps = HTMLAttributes<HTMLDivElement>

export function PromptInputActions({ children, className, ...props }: PromptInputActionsProps) {
  return (
    <div className={cn('flex items-center gap-2 px-4 py-2 border-t border-[var(--border)]', className)} {...props}>
      <span className="text-xs text-[var(--text-muted)] mr-auto">Ctrl+Enter to run</span>
      {children}
    </div>
  )
}

export type PromptInputActionSendProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  onStop?: () => void
}

export function PromptInputActionSend({ onStop, className, ...props }: PromptInputActionSendProps) {
  const { value, status, disabled } = usePromptInput()
  const busy = status === 'streaming' || status === 'submitted'

  if (busy && onStop) {
    return (
      <button
        type="button"
        onClick={onStop}
        className={cn('btn btn-sm flex items-center gap-1.5 bg-white/8 text-[var(--text-primary)] border border-[var(--border)] hover:bg-white/14', className)}
        {...props}
      >
        <Square size={12} className="fill-current" />
        Stop
      </button>
    )
  }

  return (
    <button
      type="submit"
      disabled={disabled || busy || !value.trim()}
      aria-label="Run task"
      className={cn('btn btn-primary btn-sm flex items-center gap-1.5', className)}
      {...props}
    >
      {busy
        ? <><span className="inline-block w-3 h-3 border-2 border-white/25 border-t-white rounded-full animate-spin" /> Running…</>
        : <><Send size={13} /> Run task</>}
    </button>
  )
}
