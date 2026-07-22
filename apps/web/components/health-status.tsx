"use client";
import { useCallback, useEffect, useState } from "react";
import { getHealth } from "@/lib/api";
import { Button, Skeleton } from "./ui";
import { Icon } from "./icons";
export function HealthStatus() {
  const [state, setState] = useState<"loading" | "online" | "offline">(
    "loading",
  );
  const [updatedAt, setUpdatedAt] = useState<string>();
  const check = useCallback(() => {
    setState("loading");
    const controller = new AbortController();
    getHealth(controller.signal)
      .then(() => setState("online"))
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError"))
          setState("offline");
      })
      .finally(() =>
        setUpdatedAt(
          new Intl.DateTimeFormat("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }).format(new Date()),
        ),
      );
    return controller;
  }, []);
  useEffect(() => {
    const controller = check();
    return () => controller.abort();
  }, [check]);
  if (state === "loading")
    return (
      <div role="status" aria-live="polite" className="space-y-3">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-10 w-full" />
        <span className="sr-only">正在连接后端…</span>
      </div>
    );
  return (
    <div
      role="status"
      aria-live="polite"
      data-state={state}
      className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <div>
        <p className="flex items-center gap-2 font-semibold">
          <span
            className={`h-2.5 w-2.5 rounded-full ${state === "online" ? "bg-emerald-500" : "bg-red-500"}`}
          />
          {state === "online"
            ? "后端服务已连接"
            : "后端暂不可用，请确认 API 已启动"}
        </p>
        <p className="mt-1 text-xs text-[var(--text-secondary)]">
          真实 /health 检查 · 更新于 {updatedAt}
        </p>
      </div>
      <Button variant="outline" onClick={check}>
        <Icon name="refresh" className="h-4 w-4" />
        重新检查
      </Button>
    </div>
  );
}
