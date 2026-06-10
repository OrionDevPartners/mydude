import { type ReactNode, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import { logout } from '@/lib/api'
import {
  LayoutDashboard, History, Key, Globe, Plug, Zap,
  ShieldCheck, GitBranch, Brain, Activity, CreditCard,
  Cpu, CircleDollarSign, Heart, UserSquare, LogOut, Menu, X, Bot
} from 'lucide-react'

interface NavItem { label: string; to: string; icon: ReactNode; section?: string }

const NAV: NavItem[] = [
  { label: 'Dashboard', to: '/', icon: <LayoutDashboard size={16} />, section: 'MAIN' },
  { label: 'Task History', to: '/history', icon: <History size={16} /> },
  { label: 'API Vault', to: '/keys', icon: <Key size={16} />, section: 'SERVICES' },
  { label: 'Directory', to: '/directory', icon: <Globe size={16} /> },
  { label: 'Connected', to: '/connected', icon: <Plug size={16} /> },
  { label: 'Capabilities', to: '/capabilities', icon: <Zap size={16} />, section: 'GOVERNANCE' },
  { label: 'Governance', to: '/governance', icon: <ShieldCheck size={16} /> },
  { label: 'Provenance', to: '/provenance', icon: <GitBranch size={16} /> },
  { label: 'Memory', to: '/memory', icon: <Brain size={16} /> },
  { label: 'System Health', to: '/system', icon: <Activity size={16} /> },
  { label: 'Subscriptions', to: '/subscriptions', icon: <CreditCard size={16} />, section: 'TOOLS' },
  { label: 'Finance', to: '/finance', icon: <CircleDollarSign size={16} /> },
  { label: 'Coach', to: '/coach', icon: <Heart size={16} /> },
  { label: 'Avatar', to: '/avatar', icon: <UserSquare size={16} /> },
  { label: 'Local AI Models', to: '/local-models', icon: <Cpu size={16} /> },
  { label: 'Bot Fleet', to: '/fleet', icon: <Bot size={16} /> },
]

function SidebarContent({ onNavClick }: { onNavClick?: () => void }) {
  const { branding } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: 'rgba(8,12,24,0.98)', borderRight: '1px solid var(--border)',
      padding: '16px 10px', overflowY: 'auto',
    }}>
      {/* Logo */}
      <div style={{ padding: '2px 10px 16px', marginBottom: 4, borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontSize: 17, fontWeight: 800, letterSpacing: '-0.3px', lineHeight: 1.2 }}>
          <span style={{ color: 'var(--accent)' }}>MyDude</span>
          <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>.io</span>
        </div>
        <div style={{ fontSize: 10.5, color: 'var(--text-muted)', marginTop: 3 }}>{branding.tagline}</div>
      </div>

      <div style={{ flex: 1, paddingTop: 8 }}>
        {NAV.map((item) => (
          <div key={item.to}>
            {item.section && (
              <div className="nav-section">{item.section}</div>
            )}
            <NavLink
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
              onClick={onNavClick}
            >
              <span style={{ opacity: 0.75 }}>{item.icon}</span>
              {item.label}
            </NavLink>
          </div>
        ))}
      </div>

      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10, marginTop: 8 }}>
        <button
          className="btn btn-ghost"
          onClick={handleLogout}
          style={{ justifyContent: 'flex-start', width: '100%', gap: 9 }}
        >
          <LogOut size={15} style={{ opacity: 0.7 }} />
          Sign out
        </button>
      </div>
    </div>
  )
}

export function Layout({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <>
      <div style={{ display: 'flex', minHeight: '100vh' }}>
        {/* Sidebar — hidden on mobile, sticky on desktop */}
        <div className="sidebar-desktop" style={{ width: 'var(--sidebar-width)', flexShrink: 0, position: 'sticky', top: 0, height: '100vh' }}>
          <SidebarContent />
        </div>

        {/* Main area */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          {/* Mobile top bar */}
          <header className="mobile-topbar" style={{
            display: 'flex', alignItems: 'center', gap: 12,
            padding: '12px 16px', borderBottom: '1px solid var(--border)',
            background: 'rgba(8,12,24,0.95)', backdropFilter: 'blur(8px)',
            position: 'sticky', top: 0, zIndex: 30,
          }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setMobileOpen(true)}
              style={{ padding: '6px' }}
            >
              <Menu size={20} />
            </button>
            <span style={{ fontSize: 16, fontWeight: 800 }}>
              <span style={{ color: 'var(--accent)' }}>MyDude</span>
              <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>.io</span>
            </span>
          </header>

          <main style={{
            flex: 1, padding: '24px 20px',
            maxWidth: 1100, width: '100%', margin: '0 auto',
          }}>
            {children}
          </main>
        </div>
      </div>

      {/* Mobile drawer overlay */}
      {mobileOpen && (
        <>
          <div
            style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 40 }}
            onClick={() => setMobileOpen(false)}
          />
          <div style={{
            position: 'fixed', left: 0, top: 0, bottom: 0,
            width: 240, zIndex: 50,
          }}>
            <div style={{ position: 'absolute', top: 12, right: -40, zIndex: 51 }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setMobileOpen(false)}
                style={{ color: 'white' }}
              >
                <X size={18} />
              </button>
            </div>
            <SidebarContent onNavClick={() => setMobileOpen(false)} />
          </div>
        </>
      )}

      <style>{`
        .sidebar-desktop { display: none; }
        .mobile-topbar { display: flex; }
        @media (min-width: 768px) {
          .sidebar-desktop { display: block; }
          .mobile-topbar { display: none !important; }
        }
      `}</style>
    </>
  )
}
