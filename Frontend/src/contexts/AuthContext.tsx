import { clearCache } from '../cache';

import { useState, useEffect, useCallback } from 'react';
import { getErrorMessage } from '../http/api-error';
import { AUTH_SESSION_EXPIRED_EVENT } from '../http/client';
import { useHttpFetcher } from '../hooks/useHttpFetcher';
import type {
  AuthProviderProps,
  LoginRequest,
  LoginResponse,
  ProtectedRouteProps,
  MessageResponse,
  User,
} from '../../typefiles';
import { AuthContext, useAuth } from './useAuth';




const TOKEN_KEY = 'auth_token';
const USER_KEY = 'auth_user';

export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const { fetchIt } = useHttpFetcher();

  const validateToken = useCallback(async () => {
    try {
      const userData = await fetchIt<User>({
        apiEndPoint: 'auth/me',
        httpMethod: 'get',
      });

      setUser(userData);
      localStorage.setItem(USER_KEY, JSON.stringify(userData));
    } catch {
      // Network errors preserve the cached user. A 401 is cleared by the
      // transport's session-expired event.
    } finally {
      setIsLoading(false);
    }
  }, [fetchIt]);

  useEffect(() => {
    const handleExpiredSession = () => {
      clearCache();
      setToken(null);
      setUser(null);
    };
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, handleExpiredSession);
    return () => window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, handleExpiredSession);
  }, []);

  // Load saved session on mount
  useEffect(() => {
    const savedToken = localStorage.getItem(TOKEN_KEY);
    const savedUser = localStorage.getItem(USER_KEY);
    
    if (savedToken && savedUser) {
      setToken(savedToken);
      try {
        setUser(JSON.parse(savedUser));
      } catch {
        localStorage.removeItem(USER_KEY);
      }
      // Validate token
      void validateToken();
    } else {
      setIsLoading(false);
    }
  }, [validateToken]);

  const login = useCallback(async (email: string, password: string) => {
    try {
      const reqData: LoginRequest = { email, password };
      const result = await fetchIt<LoginResponse, LoginRequest>({
        apiEndPoint: 'auth/login',
        httpMethod: 'post',
        reqData,
        auth: 'none',
        timeoutMs: 15_000,
      });
      
      // Save session
      setToken(result.token);
      setUser(result.user);
      localStorage.setItem(TOKEN_KEY, result.token);
      localStorage.setItem(USER_KEY, JSON.stringify(result.user));
      
      return { success: true };
    } catch (error: unknown) {
      return { success: false, error: getErrorMessage(error) };
    }
  }, [fetchIt]);

  const logout = useCallback(async () => {
    if (token) {
      try {
        await fetchIt<MessageResponse>({
          apiEndPoint: 'auth/logout',
          httpMethod: 'post',
        });
      } catch {
        // Ignore logout API errors
      }
    }
    
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    clearCache();
    setToken(null);
    setUser(null);
  }, [fetchIt, token]);

  const refreshUser = useCallback(async () => {
    if (token) {
      await validateToken();
    }
  }, [token, validateToken]);

  const isAuthenticated = !!user && !!token;
  const isAdmin = user?.role === 'admin';

  return (
    <AuthContext.Provider value={{
      user,
      token,
      isLoading,
      isAuthenticated,
      isAdmin,
      login,
      logout,
      refreshUser,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

// Protected route component
import { Navigate, useLocation } from 'react-router-dom';

export function ProtectedRoute({ children, requireAdmin = false }: ProtectedRouteProps) {
  const { isAuthenticated, isAdmin, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600"></div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (requireAdmin && !isAdmin) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}
