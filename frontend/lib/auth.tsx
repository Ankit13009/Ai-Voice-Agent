"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";

import { authApi } from "@/lib/api/endpoints";
import { setSessionExpiredHandler, tokenStore } from "@/lib/api/client";
import type { User } from "@/types/api";

/**
 * Session state.
 *
 * On mount the app calls `/auth/me` rather than trusting the stored token's
 * contents. The token is opaque to the client by design, and a user whose role
 * changed or whose account was deactivated must not keep the access their last
 * login granted.
 */

interface AuthContextValue {
  user: User | null;
  /** True until the initial session check resolves. Gate rendering on it. */
  loading: boolean;
  /** Returns the signed-in user so callers can route by role. */
  login: (email: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const clearSession = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  // The API client calls this when a refresh fails, so an expired session ends
  // at the login screen instead of leaving a shell with permanent 401s.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      clearSession();
      router.replace("/login");
    });
    return () => setSessionExpiredHandler(null);
  }, [clearSession, router]);

  const loadUser = useCallback(async () => {
    if (!tokenStore.access) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await authApi.me());
    } catch {
      // Any failure here means the session is unusable; the client has already
      // attempted a refresh by this point.
      clearSession();
    } finally {
      setLoading(false);
    }
  }, [clearSession]);

  useEffect(() => {
    void loadUser();
  }, [loadUser]);

  const login = useCallback(async (email: string, password: string) => {
    const result = await authApi.login({ email, password });
    setUser(result.user);
    return result.user;
  }, []);

  const logout = useCallback(async () => {
    await authApi.logout();
    setUser(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo(
    () => ({ user, loading, login, logout, refreshUser: loadUser }),
    [user, loading, login, logout, loadUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside an AuthProvider.");
  return context;
}
