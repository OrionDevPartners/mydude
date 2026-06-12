import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { getMe, getBranding, ApiError, setUnauthorizedHandler } from '@/lib/api'

interface Branding { name: string; short_name: string; tagline: string }
interface CurrentUser { username: string | null; is_admin: boolean; dev_bypass: boolean }
interface AuthCtx {
  authenticated: boolean | null
  user: CurrentUser | null
  branding: Branding
  refetch: () => void
}

const AuthContext = createContext<AuthCtx>({
  authenticated: null,
  user: null,
  branding: { name: 'MyDude.io', short_name: 'MyDude', tagline: 'AI Business Automation Platform' },
  refetch: () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [branding, setBranding] = useState<Branding>({
    name: 'MyDude.io',
    short_name: 'MyDude',
    tagline: 'AI Business Automation Platform',
  })

  async function check() {
    try {
      const me = await getMe()
      setUser({ username: me.username, is_admin: me.is_admin, dev_bypass: me.dev_bypass })
      setAuthenticated(true)
    } catch (e) {
      setUser(null)
      if (e instanceof ApiError && e.status === 401) setAuthenticated(false)
      else setAuthenticated(false)
    }
  }

  useEffect(() => {
    setUnauthorizedHandler(() => setAuthenticated(false))
    check()
    getBranding().then(setBranding).catch(() => {})
    return () => setUnauthorizedHandler(null)
  }, [])

  return (
    <AuthContext.Provider value={{ authenticated, user, branding, refetch: check }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
