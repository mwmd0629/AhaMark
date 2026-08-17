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

export function AuthGate({
  children,
  audience = "teacher",
}: {
  children: ReactNode;
  audience?: "teacher" | "student";
}) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  useEffect(() => {
    authApi
      .me()
      .then((nextUser) => {
        const roles = nextUser.roles ?? [];
        const studentOnly =
          roles.includes("student") && !roles.includes("teacher");
        if (audience === "student" && !roles.includes("student")) {
          router.replace("/dashboard");
          return;
        }
        if (audience === "teacher" && studentOnly) {
          router.replace("/student");
          return;
        }
        setUser(nextUser);
      })
      .catch(() => router.replace("/login"));
  }, [audience, router]);
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
