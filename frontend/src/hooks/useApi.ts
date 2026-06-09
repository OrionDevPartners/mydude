import { useState, useEffect, useCallback, DependencyList } from 'react'
import { ApiError } from '@/lib/api'

interface State<T> { data: T | null; loading: boolean; error: string | null }

export function useApi<T>(
  fn: () => Promise<T>,
  deps: DependencyList = [],
  immediate = true
): State<T> & { refetch: () => void } {
  const [state, setState] = useState<State<T>>({ data: null, loading: immediate, error: null })

  const fetch = useCallback(async () => {
    setState(s => ({ ...s, loading: true, error: null }))
    try {
      const data = await fn()
      setState({ data, loading: false, error: null })
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : 'An error occurred'
      setState({ data: null, loading: false, error: msg })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => { if (immediate) fetch() }, [fetch, immediate])

  return { ...state, refetch: fetch }
}

export function useMutation<TArgs, TResult>(
  fn: (args: TArgs) => Promise<TResult>
): {
  mutate: (args: TArgs) => Promise<TResult | null>
  loading: boolean
  error: string | null
  reset: () => void
} {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function mutate(args: TArgs): Promise<TResult | null> {
    setLoading(true)
    setError(null)
    try {
      const result = await fn(args)
      setLoading(false)
      return result
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : 'An error occurred'
      setError(msg)
      setLoading(false)
      return null
    }
  }

  return { mutate, loading, error, reset: () => setError(null) }
}
