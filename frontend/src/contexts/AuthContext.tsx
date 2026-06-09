import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { getMe, getBranding, ApiError } from '@/lib/api'

interface Branding { name: string; short_name: string; tagline: string }
interface AuthCtx {
  authenticated: boolean | null
  branding: Branding
  refetch: () => void
}

const AuthContext = createContext<AuthCtx>({
  authenticated: null,
  branding: { name: 'MyDude.io', short_name: 'MyDude', tagline: 'AI Business Automation Platform' },
  refetch: () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)
  const [branding, setBranding] = useState<Branding>({
    name: 'MyDude.io',
    short_name: 'MyDude',
    tagline: 'AI Business Automation Platform',
  })

  async function check() {
    try {
      await getMe()
      setAuthenticated(true)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setAuthenticated(false)
      else setAuthenticated(false)
    }
  }

  useEffect(() => {
    check()
    getBranding().then(setBranding).catch(() => {})
  }, [])

  return (
    <AuthContext.Provider value={{ authenticated, branding, refetch: check }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
