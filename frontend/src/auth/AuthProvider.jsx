import { createContext, useContext, useMemo, useState, useCallback, useEffect } from 'react';
import * as api from '../services/verificationService';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [initializing, setInitializing] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.getCurrentUser()
      .then((u) => { if (!cancelled) setUser(u); })
      .catch(() => { if (!cancelled) setUser(null); })
      .finally(() => { if (!cancelled) setInitializing(false); });
    return () => { cancelled = true; };
  }, []);

  const login = useCallback(async ({ email, password }) => {
    const next = await api.login(email, password);
    setUser(next);
    return next;
  }, []);

  const logout = useCallback(async () => {
    setUser(null);
    await api.logout().catch(() => {});
  }, []);

  const value = useMemo(
    () => ({ user, isAuthenticated: !!user, login, logout }),
    [user, login, logout]
  );

  // Blocks rendering (including the router) until the initial session check
  // resolves — without this, ProtectedRoute would see isAuthenticated=false
  // on every hard refresh and redirect to /login before /api/auth/me even
  // returns.
  if (initializing) return null;

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
