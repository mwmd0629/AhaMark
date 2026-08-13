"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { ApiError, authApi } from "@/lib/api";
import { landingPath } from "@/lib/auth-routing";
import { studentApi, type StudentProfile } from "@/lib/student-api";

const StudentContext = createContext<StudentProfile | null>(null);

export function useStudent() {
  return useContext(StudentContext);
}

export function StudentAuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [student, setStudent] = useState<StudentProfile | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const user = await authApi.me();
        if (!active) return;
        const surface = user.must_change_password
          ? "change_password"
          : user.landing_surface;
        if (surface !== "student") {
          const destination = landingPath(surface);
          if (destination) {
            router.replace(destination);
            return;
          }
          await authApi.logout().catch(() => undefined);
          if (active) router.replace("/login");
          return;
        }
        const profile = await studentApi.me();
        if (active) setStudent(profile);
      } catch (reason: unknown) {
        if (!active) return;
        if (reason instanceof ApiError && reason.status === 401) {
          router.replace("/login");
          return;
        }
        setError(
          reason instanceof ApiError
            ? reason.message
            : "无法验证学生身份，请稍后重试。",
        );
      }
    })();
    return () => {
      active = false;
    };
  }, [router]);

  if (error) {
    return (
      <main className="grid min-h-screen place-items-center p-6">
        <div
          role="alert"
          className="max-w-md rounded-2xl border border-red-200 bg-white p-6 text-center shadow-sm"
        >
          <h1 className="text-lg font-bold text-red-800">无法进入学生端</h1>
          <p className="mt-2 text-sm leading-6 text-red-700">{error}</p>
          <Link
            href="/login"
            className="mt-5 inline-flex min-h-10 items-center rounded-xl bg-[var(--brand-600)] px-4 text-sm font-semibold text-white"
          >
            返回登录
          </Link>
        </div>
      </main>
    );
  }

  if (!student) {
    return (
      <div
        role="status"
        className="grid min-h-screen place-items-center text-sm text-slate-500"
      >
        正在验证学生身份…
      </div>
    );
  }

  return (
    <StudentContext.Provider value={student}>
      {children}
    </StudentContext.Provider>
  );
}
