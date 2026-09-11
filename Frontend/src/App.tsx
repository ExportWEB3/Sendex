import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Sidebar, ToastContainer, ConfirmProvider } from './components';
import { FleetAssistant } from './components';
import { AuthProvider, ProtectedRoute } from './contexts/AuthContext';
import { useAuth } from './contexts/useAuth';
import { ThemeProvider } from './contexts/ThemeContext';
import type { AppLayoutProps } from '../typefiles';

const Campaigns = lazy(() => import('./pages/Campaigns').then(({ Campaigns: page }) => ({ default: page })));
const Dashboard = lazy(() => import('./pages/Dashboard').then(({ Dashboard: page }) => ({ default: page })));
const ForgotPassword = lazy(() => import('./pages/ForgotPassword').then(({ ForgotPassword: page }) => ({ default: page })));
const Inboxes = lazy(() => import('./pages/Inboxes').then(({ Inboxes: page }) => ({ default: page })));
const Lists = lazy(() => import('./pages/Lists').then(({ Lists: page }) => ({ default: page })));
const Login = lazy(() => import('./pages/Login').then(({ Login: page }) => ({ default: page })));
const Queue = lazy(() => import('./pages/Queue').then(({ Queue: page }) => ({ default: page })));
const Register = lazy(() => import('./pages/Register').then(({ Register: page }) => ({ default: page })));
const RepliesResend = lazy(() => import('./pages/RepliesResend').then(({ RepliesResend: page }) => ({ default: page })));
const RepliesSMTP = lazy(() => import('./pages/RepliesSMTP').then(({ RepliesSMTP: page }) => ({ default: page })));
const Settings = lazy(() => import('./pages/Settings').then(({ Settings: page }) => ({ default: page })));
const SmtpAccounts = lazy(() => import('./pages/SmtpAccounts').then(({ SmtpAccounts: page }) => ({ default: page })));
const Templates = lazy(() => import('./pages/Templates'));

function RouteLoader() {
  return (
    <div className="flex min-h-48 items-center justify-center bg-gray-100" role="status" aria-label="Loading page">
      <div className="h-10 w-10 animate-spin border-2 border-gray-300 border-t-indigo-600" />
    </div>
  );
}

// Layout for authenticated pages
function AppLayout({ children }: AppLayoutProps) {
  return (
    <div className="flex h-screen bg-gray-100">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <main className="flex-1 overflow-auto">
          <Suspense fallback={<RouteLoader />}>
            {children}
          </Suspense>
        </main>
        <footer className="bg-[#040507] text-[#7f9cb5] text-center py-1.5 text-[11px] tracking-[0.08em] font-mono border-t border-[#1d2b38] shrink-0 uppercase">
          FLEETCTRL-X Core v1.2.6 &mdash; By Th3 N3w G3n3ration Tak3rz
        </footer>
      </div>
      <FleetAssistant />
      <ToastContainer />
    </div>
  );
}

// Protected routes wrapper
function ProtectedRoutes() {
  return (
    <ProtectedRoute>
      <AppLayout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/inboxes" element={<Inboxes />} />
          <Route path="/campaigns" element={<Campaigns />} />
          <Route path="/replies" element={<Navigate to="/replies/resend" replace />} />
          <Route path="/replies/resend" element={<RepliesResend />} />
          <Route path="/replies/ses" element={<Navigate to="/replies/resend" replace />} />
          <Route path="/replies/smtp" element={<RepliesSMTP />} />
          <Route path="/lists" element={<Lists />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/smtp" element={<SmtpAccounts />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </AppLayout>
    </ProtectedRoute>
  );
}

function AppRoutes() {
  const { isAuthenticated, isLoading } = useAuth();
  
  if (isLoading) {
    return <RouteLoader />;
  }

  return (
    <Suspense fallback={<RouteLoader />}>
      <Routes>
        {/* Public auth routes */}
        <Route path="/login" element={isAuthenticated ? <Navigate to="/" /> : <Login />} />
        <Route path="/register" element={isAuthenticated ? <Navigate to="/" /> : <Register />} />
        <Route path="/forgot-password" element={isAuthenticated ? <Navigate to="/" /> : <ForgotPassword />} />
        
        {/* Protected routes */}
        <Route path="/*" element={<ProtectedRoutes />} />
      </Routes>
    </Suspense>
  );
}

function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <AuthProvider>
          <ConfirmProvider>
            <AppRoutes />
          </ConfirmProvider>
        </AuthProvider>
      </BrowserRouter>
    </ThemeProvider>
  );
}

export default App;
