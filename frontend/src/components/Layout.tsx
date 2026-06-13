import { type ReactNode, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import { logout } from '@/lib/api'
import {
  LayoutDashboard, History, Key, Globe, Plug, Zap,
  ShieldCheck, GitBranch, Brain, Activity, CreditCard,
  Cpu, CircleDollarSign, Heart, UserSquare, LogOut, Menu, X, Bot, Sparkles, FlaskConical,
  Users as UsersIcon, User as UserIcon, ChevronRight,
} from 'lucide-react'

interface NavItem { label: string; to: string; icon: ReactNode; section?: string }

const NAV: NavItem[] = [
  { label: 'Dashboard',     to: '/',             icon: <LayoutDashboard size={15} />, section: 'MAIN' },
  { label: 'Task History',  to: '/history',      icon: <History size={15} /> },
  { label: 'API Vault',     to: '/keys',         icon: <Key size={15} />,   section: 'SERVICES' },
  { label: 'Directory',     to: '/directory',    icon: <Globe size={15} /> },
  { label: 'Connected',     to: '/connected',    icon: <Plug size={15} /> },
  { label: 'Capabilities',  to: '/capabilities', icon: <Zap size={15} />,   section: 'GOVERNANCE' },
  { label: 'Governance',    to: '/governance',   icon: <ShieldCheck size={15} /> },
  { label: 'Provenance',    to: '/provenance',   icon: <GitBranch size={15} /> },
  { label: 'Prompt Engine', to: '/prompt-engine',icon: <Sparkles size={15} /> },
  { label: 'Evolution Loop',to: '/evolution',    icon: <FlaskConical size={15} /> },
  { label: 'Memory',        to: '/memory',       icon: <Brain size={15} /> },
  { label: 'System Health', to: '/system',       icon: <Activity size={15} /> },
  { label: 'Subscriptions', to: '/subscriptions',icon: <CreditCard size={15} />, section: 'TOOLS' },
  { label: 'Finance',       to: '/finance',      icon: <CircleDollarSign size={15} /> },
  { label: 'Coach',         to: '/coach',        icon: <Heart size={15} /> },
  { label: 'Avatar',        to: '/avatar',       icon: <UserSquare size={15} /> },
  { label: 'Local AI Models',to: '/local-models',icon: <Cpu size={15} /> },
  { label: 'Bot Fleet',     to: '/fleet',        icon: <Bot size={15} /> },
]

function SidebarContent({ onNavClick }: { onNavClick?: () => void }) {
  const { branding, user, refetch } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    try { await logout() } finally {
      await refetch()
      navigate('/login')
    }
  }

  const nav: NavItem[] = user?.is_admin
    ? [...NAV, { label: 'Users', to: '/users', icon: <UsersIcon size={15} /> }]
    : NAV

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: 'rgba(5,8,16,0.92)',
      backdropFilter: 'blur(var(--glass-blur-heavy))',
      WebkitBackdropFilter: 'blur(var(--glass-blur-heavy))',
      borderRight: '1px solid var(--glass-border)',
      padding: '0 8px', overflowY: 'auto',
    }}>
      {/* Logo */}
      <div style={{
        padding: '20px 10px 16px',
        borderBottom: '1px solid var(--glass-border)',
        marginBottom: 6,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          {/* Logo mark */}
          <div style={{
            width: 30, height: 30, borderRadius: 8, flexShrink: 0,
            background: 'linear-gradient(135deg, rgba(233,69,96,0.3) 0%, rgba(124,92,191,0.2) 100%)',
            border: '1px solid rgba(233,69,96,0.35)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: '0 0 12px rgba(233,69,96,0.15)',
          }}>
            <span style={{ fontSize: 13, fontWeight: 800, color: 'var(--accent)', lineHeight: 1 }}>M</span>
          </div>
          <div>
            <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: '-0.3px', lineHeight: 1.1 }}>
              <span style={{ color: 'var(--accent)' }}>MyDude</span>
              <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>.io</span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 1, letterSpacing: '0.02em' }}>
              {branding.tagline}
            </div>
          </div>
        </div>
      </div>

      {/* Nav items */}
      <div style={{ flex: 1, paddingTop: 4, paddingBottom: 8 }}>
        {nav.map((item) => (
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
              <span style={{ opacity: 0.7, display: 'flex', alignItems: 'center' }}>{item.icon}</span>
              <span style={{ flex: 1 }}>{item.label}</span>
              <ChevronRight size={11} style={{ opacity: 0.3, flexShrink: 0 }} />
            </NavLink>
          </div>
        ))}
      </div>

      {/* Footer */}
      <div style={{
        borderTop: '1px solid var(--glass-border)',
        paddingTop: 10, paddingBottom: 10, marginTop: 4,
      }}>
        {user?.username && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '6px 10px 10px',
            fontSize: 12, color: 'var(--text-secondary)',
          }}>
            <div style={{
              width: 24, height: 24, borderRadius: 6, flexShrink: 0,
              background: 'var(--bg-glass-active)',
              border: '1px solid var(--glass-border-strong)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <UserIcon size={12} style={{ opacity: 0.8 }} />
            </div>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
              {user.username}
              {user.is_admin && (
                <span style={{
                  marginLeft: 5, fontSize: 9.5, color: 'var(--accent)',
                  background: 'var(--accent-dim)', border: '1px solid var(--border-accent)',
                  borderRadius: 4, padding: '1px 5px', fontWeight: 700,
                  textTransform: 'uppercase', letterSpacing: '0.05em',
                }}>
                  admin
                </span>
              )}
            </span>
          </div>
        )}
        <button
          className="btn btn-ghost btn-sm"
          onClick={handleLogout}
          style={{ justifyContent: 'flex-start', width: '100%', gap: 9, color: 'var(--text-secondary)' }}
        >
          <LogOut size={14} style={{ opacity: 0.65 }} />
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
        {/* Sidebar — sticky on desktop */}
        <div
          className="sidebar-desktop"
          style={{ width: 'var(--sidebar-width)', flexShrink: 0, position: 'sticky', top: 0, height: '100vh' }}
        >
          <SidebarContent />
        </div>

        {/* Main content area */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          {/* Mobile top bar */}
          <header
            className="mobile-topbar"
            style={{
              display: 'flex', alignItems: 'center', gap: 12,
              padding: '12px 16px',
              borderBottom: '1px solid var(--glass-border)',
              background: 'rgba(5,8,16,0.92)',
              backdropFilter: 'blur(var(--glass-blur-heavy))',
              WebkitBackdropFilter: 'blur(var(--glass-blur-heavy))',
              position: 'sticky', top: 0, zIndex: 30,
            }}
          >
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setMobileOpen(true)}
              style={{ padding: '6px' }}
            >
              <Menu size={20} />
            </button>
            <span style={{ fontSize: 15, fontWeight: 800 }}>
              <span style={{ color: 'var(--accent)' }}>MyDude</span>
              <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>.io</span>
            </span>
          </header>

          <main style={{
            flex: 1, padding: '28px 24px',
            maxWidth: 1100, width: '100%', margin: '0 auto',
          }}>
            {children}
          </main>
        </div>
      </div>

      {/* Mobile drawer */}
      {mobileOpen && (
        <>
          <div
            style={{
              position: 'fixed', inset: 0,
              background: 'rgba(0,0,0,0.65)',
              backdropFilter: 'blur(4px)',
              zIndex: 40,
            }}
            onClick={() => setMobileOpen(false)}
          />
          <div style={{ position: 'fixed', left: 0, top: 0, bottom: 0, width: 244, zIndex: 50 }}>
            <div style={{ position: 'absolute', top: 14, right: -44, zIndex: 51 }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setMobileOpen(false)}
                style={{ color: 'var(--text-primary)', padding: 8 }}
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
        .mobile-topbar   { display: flex; }
        @media (min-width: 768px) {
          .sidebar-desktop { display: block; }
          .mobile-topbar   { display: none !important; }
        }
      `}</style>
    </>
  )
}
