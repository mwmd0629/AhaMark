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
  audience?: "teacher" | "student" | "admin";
}) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  useEffect(() => {
    authApi
      .me()
      .then((nextUser) => {
        const roles = nextUser.roles ?? [];
        const isAdmin = roles.includes("admin");
        const isStudent = roles.includes("student");
        const isTeacher = roles.includes("teacher") || roles.length === 0;
        if (audience === "student" && !roles.includes("student")) {
          router.replace(isAdmin ? "/admin/accounts" : "/dashboard");
          return;
        }
        if (audience === "admin" && !isAdmin) {
          router.replace(isStudent ? "/student" : "/dashboard");
          return;
        }
        if (audience === "teacher" && !isTeacher) {
          router.replace(isAdmin ? "/admin/accounts" : "/student");
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
