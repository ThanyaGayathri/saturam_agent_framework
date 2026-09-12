import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const PrototypeAuthContext = createContext(null);

async function fetchSession() {
  try {
    const r = await fetch("/api/auth/session", { credentials: "include" });
    const data = await r.json().catch(() => ({}));
    if (r.status === 503) {
      return { authDisabled: false, authenticated: false, misconfigured: true };
    }
    if (!r.ok) {
      return { authDisabled: false, authenticated: false, misconfigured: false };
    }
    return {
      authDisabled: data.authDisabled === true,
      authenticated: data.authenticated === true,
      misconfigured: false,
    };
  } catch {
    if (import.meta.env.DEV) {
      return {
        authDisabled: true,
        authenticated: true,
        misconfigured: false,
        devBypass: true,
      };
    }
    return { authDisabled: false, authenticated: false, misconfigured: false };
  }
}

export function PrototypeAuthProvider({ children }) {
  const [ready, setReady] = useState(false);
  const [authDisabled, setAuthDisabled] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [misconfigured, setMisconfigured] = useState(false);

  const refresh = useCallback(async () => {
    const s = await fetchSession();
    setAuthDisabled(s.authDisabled);
    setAuthenticated(s.authenticated);
    setMisconfigured(Boolean(s.misconfigured));
    setReady(true);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (username, password, remember) => {
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ username, password, remember }),
      });
      const data = await r.json().catch(() => ({}));
      if (data.authDisabled) {
        setAuthDisabled(true);
        setAuthenticated(true);
        setMisconfigured(false);
        return { ok: true };
      }
      if (r.status === 503) {
        return { ok: false, error: "server", message: data.error || "Login service unavailable" };
      }
      if (!r.ok || !data.ok) {
        return { ok: false, error: "credentials" };
      }
      setAuthDisabled(false);
      setAuthenticated(true);
      setMisconfigured(false);
      return { ok: true };
    } catch (e) {
      if (import.meta.env.DEV) {
        setAuthDisabled(true);
        setAuthenticated(true);
        return { ok: true, devBypass: true };
      }
      return { ok: false, error: "network" };
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
    } catch {
      /* ignore */
    }
    setAuthenticated(false);
    await refresh();
  }, [refresh]);

  const authEnabled = !authDisabled;
  const isAuthenticated = authDisabled || authenticated;

  const value = useMemo(
    () => ({
      ready,
      authEnabled,
      isAuthenticated,
      misconfigured,
      login,
      logout,
      refresh,
    }),
    [ready, authEnabled, isAuthenticated, misconfigured, login, logout, refresh]
  );

  return <PrototypeAuthContext.Provider value={value}>{children}</PrototypeAuthContext.Provider>;
}

export function usePrototypeAuth() {
  const ctx = useContext(PrototypeAuthContext);
  if (!ctx) {
    throw new Error("usePrototypeAuth must be used within PrototypeAuthProvider");
  }
  return ctx;
}
