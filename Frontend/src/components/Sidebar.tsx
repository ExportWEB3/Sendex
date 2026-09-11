import { NavLink, useLocation } from 'react-router-dom';
import { 
  LayoutDashboard, 
  Inbox, 
  Megaphone, 
  Users, 
  Layers, 
  Server,
  Settings,
  Mail,
  Menu,
  X,
  MessageSquareReply,
  LogOut,
  User,
  Sun,
  Moon,
  FileText,
  ChevronDown,
  Zap,
  Globe
} from 'lucide-react';
import { useState } from 'react';
import { useAuth } from '../contexts/useAuth';
import { useTheme } from '../contexts/useTheme';
import type { NavItem } from '../../typefiles';

const navItems: NavItem[] = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/inboxes', icon: Inbox, label: 'Inboxes' },
  { to: '/campaigns', icon: Megaphone, label: 'Campaigns' },
  { to: '/replies', icon: MessageSquareReply, label: 'Replies', children: [
    { to: '/replies/resend', icon: Zap, label: 'Resend' },
    { to: '/replies/smtp', icon: Globe, label: 'SMTP' },
  ]},
  { to: '/lists', icon: Users, label: 'Lists' },
  { to: '/templates', icon: FileText, label: 'Email Templates' },
  { to: '/queue', icon: Layers, label: 'Queue' },
  { to: '/smtp', icon: Server, label: 'SMTP Accounts' },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

export function Sidebar() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [expandedNav, setExpandedNav] = useState<string | null>(null);
  const location = useLocation();
  const { user, logout } = useAuth();
  const { isDark, toggle } = useTheme();

  return (
    <>
      {/* Mobile menu button — only show hamburger when sidebar is closed */}
      {!mobileOpen && (
        <button
          onClick={() => setMobileOpen(true)}
          className="lg:hidden fixed top-4 left-4 z-50 p-1.5 bg-[#05070a] text-[#e9f7ff] border border-[#2e465a] rounded-md shadow-lg"
        >
          <Menu size={15} />
        </button>
      )}

      {/* Backdrop */}
      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 bg-black/50 z-40"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside className={`
        fixed lg:static inset-y-0 left-0 z-40
        w-64 bg-[#040507] text-[#d5e8fa] flex flex-col border-r border-[#1d2b38]
        transform transition-transform duration-200
        ${mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
      `}>
        <div className="p-4 border-b border-[#1d2b38]">
          <div className="flex items-center justify-between">
            <h1 className="text-[1.03rem] font-semibold uppercase tracking-[0.08em] flex items-center gap-2">
              <Mail className="w-6 h-6" /> FLEETCTRL-X
            </h1>
            {/* Close button inside sidebar header on mobile */}
            <button
              onClick={() => setMobileOpen(false)}
              className="lg:hidden p-1.5 text-[#88a3bb] hover:text-[#f4fbff] hover:bg-[#0b1015] rounded-md transition-colors"
            >
              <X size={16} />
            </button>
          </div>
          <p className="text-[#7f98af] text-xs mt-1 tracking-wide">Campaign control system</p>
        </div>

        <nav className="flex-1 p-3 space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            if (item.children) {
              const isChildActive = location.pathname.startsWith(item.to + '/');
              const isOpen = isChildActive || expandedNav === item.to;
              return (
                <div key={item.to}>
                  <button
                    onClick={() => setExpandedNav(isOpen ? null : item.to)}
                    className={`
                      w-full flex items-center gap-3 px-3 py-2 rounded-md transition-colors text-sm border
                      ${isChildActive
                        ? 'bg-[#0b1218] text-[#f4fbff] border-[#305873]'
                        : 'text-[#7f9bb4] border-transparent hover:bg-[#090e13] hover:text-[#ecf8ff] hover:border-[#243748]'}
                    `}
                  >
                    <Icon size={18} />
                    <span className="flex-1 text-left">{item.label}</span>
                    <ChevronDown size={14} className={`transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                  </button>
                  {isOpen && (
                    <div className="ml-3 mt-1 space-y-1 border-l border-[#243748] pl-2">
                      {item.children.map((child) => {
                        const ChildIcon = child.icon;
                        return (
                          <NavLink
                            key={child.to}
                            to={child.to}
                            onClick={() => setMobileOpen(false)}
                            className={({ isActive }) => `
                              flex items-center gap-3 px-3 py-2 rounded-md transition-colors text-sm border
                              ${isActive
                                ? 'bg-[#0b1218] text-[#f4fbff] border-[#305873]'
                                : 'text-[#7f9bb4] border-transparent hover:bg-[#090e13] hover:text-[#ecf8ff] hover:border-[#243748]'}
                            `}
                          >
                            <ChildIcon size={16} />
                            <span>{child.label}</span>
                          </NavLink>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            }
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) => `
                  flex items-center gap-3 px-3 py-2 rounded-md transition-colors text-sm border
                  ${isActive
                    ? 'bg-[#0b1218] text-[#f4fbff] border-[#305873]'
                    : 'text-[#7f9bb4] border-transparent hover:bg-[#090e13] hover:text-[#ecf8ff] hover:border-[#243748]'}
                `}
              >
                <Icon size={18} />
                <span className="flex-1 flex items-center justify-between">
                  <span>{item.label}</span>
                </span>
              </NavLink>
            );
          })}
        </nav>

        {/* User info and logout */}
        <div className="p-3 border-t border-[#1d2b38]">
          {user && (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-8 h-8 bg-[#0a1118] border border-[#244051] rounded-md flex items-center justify-center shrink-0">
                  <User size={16} />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate text-[#eef6ff]">{user.name || user.email.split('@')[0]}</p>
                  <p className="text-xs text-[#7f9cb5] truncate">{user.email}</p>
                </div>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={toggle}
                  className="p-1.5 text-[#89a7c0] hover:text-[#f5fbff] hover:bg-[#0b1015] rounded-md border border-transparent hover:border-[#2e465a] transition-colors"
                  title={isDark ? 'Light mode' : 'Dark mode'}
                >
                  {isDark ? <Sun size={18} /> : <Moon size={18} />}
                </button>
                <button
                  onClick={logout}
                  className="p-1.5 text-[#89a7c0] hover:text-[#f5fbff] hover:bg-[#0b1015] rounded-md border border-transparent hover:border-[#2e465a] transition-colors"
                  title="Sign out"
                >
                  <LogOut size={18} />
                </button>
              </div>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
