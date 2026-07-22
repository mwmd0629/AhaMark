"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { authApi } from "@/lib/api";

export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    authApi
      .me()
      .then(() => setReady(true))
      .catch(() => router.replace("/login"));
  }, [router]);
  if (!ready)
    return (
      <div
        role="status"
        className="grid min-h-screen place-items-center text-sm text-slate-500"
      >
        正在验证登录状态…
      </div>
    );
  return children;
}
