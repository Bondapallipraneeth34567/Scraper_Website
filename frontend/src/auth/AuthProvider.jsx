import { createContext, useContext, useMemo, useState, useCallback } from 'react';

/**
 * Stand-in for the app's real AuthProvider — this repo doesn't have one yet.
 * Drop your real AuthProvider in at this same import path
 * (`src/auth/AuthProvider.jsx`, exporting `AuthProvider` + `useAuth` with
 * the same shape: { user, isAuthenticated, login, signup, logout }) and
 * every page below keeps working unchanged.
 *
 * Mock only: "login"/"signup" accept anything and never hit a server —
 * there's no password check, this is purely to make the routing/session
 * shape real so a real API swap-in doesn't touch a single page. Session
 * persists to localStorage so a refresh doesn't log you out mid-session.
 */
const AuthContext = createContext(null);

const STORAGE_KEY = 'verification-dashboard/session';

// No server-issued TTL exists yet (mock has no token), so this is a
// client-only idle expiry: a session older than this is dropped on next
// load rather than persisting forever. When a real token replaces this,
// prefer the server's own expiry (and ideally an httpOnly cookie instead
// of localStorage) over this constant.
const SESSION_TTL_MS = 12 * 60 * 60 * 1000; // 12h

function loadSession() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed?.issuedAt || Date.now() - parsed.issuedAt > SESSION_TTL_MS) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    const { issuedAt, ...user } = parsed;
    return user;
  } catch {
    return null;
  }
}

function saveSession(user) {
  try {
    if (user) localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...user, issuedAt: Date.now() }));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore — session just won't survive a refresh
  }
}

function nameFromEmail(email) {
  const local = email.split('@')[0] || 'Tester';
  return local
    .replace(/[._-]+/g, ' ')
    .split(' ')
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ');
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => loadSession());

  const login = useCallback(async ({ email }) => {
    await new Promise((r) => setTimeout(r, 400));
    const next = { id: email, name: nameFromEmail(email), email, role: 'QA Tester' };
    setUser(next);
    saveSession(next);
    return next;
  }, []);

  const signup = useCallback(async ({ name, email }) => {
    await new Promise((r) => setTimeout(r, 400));
    const next = { id: email, name: name || nameFromEmail(email), email, role: 'QA Tester' };
    setUser(next);
    saveSession(next);
    return next;
  }, []);

  const logout = useCallback(() => {
    setUser(null);
    saveSession(null);
  }, []);

  const value = useMemo(
    () => ({ user, isAuthenticated: !!user, login, signup, logout }),
    [user, login, signup, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
