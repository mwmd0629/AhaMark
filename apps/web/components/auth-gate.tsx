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

const AuthUserContext = createContext<AuthUser | null>(null);

export function useAuthUser() {
  return useContext(AuthUserContext);
}

export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  useEffect(() => {
    authApi
      .me()
      .then(setUser)
      .catch(() => router.replace("/login"));
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
