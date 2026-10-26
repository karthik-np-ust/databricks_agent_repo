import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { setUnauthorizedHandler, tokenStore } from "../api/client";
import type { Me, Role } from "../api/types";

const ORDER: Role[] = ["viewer", "operator", "approver", "admin"];

interface AuthState {
  user: Me | null;
  signIn: (token: string, user: Me) => void;
  signOut: () => void;
  /** UI convenience only: the API enforces every role check itself. */
  can: (role: Role) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

function decode(token: string | null): Me | null {
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split(".")[0].replace(/-/g, "+").replace(/_/g, "/")));
    if (payload.exp * 1000 < Date.now()) return null;
    return { name: payload.sub, role: payload.role };
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(() => decode(tokenStore.get()));
  const signOut = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);
  useEffect(() => setUnauthorizedHandler(signOut), [signOut]);
  const value = useMemo<AuthState>(
    () => ({
      user,
      signIn: (token, me) => {
        tokenStore.set(token);
        setUser(me);
      },
      signOut,
      can: (role) => !!user && ORDER.indexOf(user.role) >= ORDER.indexOf(role),
    }),
    [user, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
