"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { authApi, type AuthUser } from "@/lib/api";
import { landingPath } from "@/lib/auth-routing";

const AuthUserContext = createContext<AuthUser | null>(null);

export function useAuthUser() {
  return useContext(AuthUserContext);
}

export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const current = await authApi.me();
        if (!active) return;
        const surface = current.must_change_password
          ? "change_password"
          : current.landing_surface;
        if (surface === "teacher") {
          setUser(current);
          return;
        }
        const destination = landingPath(surface);
        if (destination) {
          router.replace(destination);
          return;
        }
        await authApi.logout().catch(() => undefined);
        if (active) router.replace("/login");
      } catch {
        if (active) router.replace("/login");
      }
    })();
    return () => {
      active = false;
    };
  }, [router]);
  if (!user)
    return (
      <div
        role="status"
        className="grid min-h-screen place-items-center text-sm text-slate-500"
      >
        正在验证登录状态…
      </div>
    );
  return (
    <AuthUserContext.Provider value={user}>{children}</AuthUserContext.Provider>
  );
}
