"use client";

import { useCallback, useEffect, useRef } from "react";

type SmartRefreshOptions = {
  enabled?: boolean;
  intervalMs?: number;
  minIntervalMs?: number;
};

export function useSmartRefresh(
  refresh: () => void | Promise<unknown>,
  {
    enabled = true,
    intervalMs,
    minIntervalMs = 5_000,
  }: SmartRefreshOptions = {},
) {
  const refreshRef = useRef(refresh);
  const inFlightRef = useRef<Promise<void> | undefined>(undefined);
  const queuedRef = useRef(false);
  const lastRefreshAtRef = useRef(0);
  refreshRef.current = refresh;

  const run = useCallback(
    (force = false): Promise<void> => {
      if (!enabled || document.visibilityState === "hidden") {
        return Promise.resolve();
      }
      if (inFlightRef.current) {
        queuedRef.current = true;
        return inFlightRef.current;
      }
      if (!force && Date.now() - lastRefreshAtRef.current < minIntervalMs) {
        return Promise.resolve();
      }
      const task = Promise.resolve()
        .then(() => refreshRef.current())
        .catch(() => undefined)
        .then(() => undefined)
        .finally(() => {
          lastRefreshAtRef.current = Date.now();
          inFlightRef.current = undefined;
          if (queuedRef.current) {
            queuedRef.current = false;
            void run(true);
          }
        });
      inFlightRef.current = task;
      return task;
    },
    [enabled, minIntervalMs],
  );

  useEffect(() => {
    lastRefreshAtRef.current = Date.now();
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void run();
    };
    const refreshWhenFocused = () => void run();
    const refreshWhenOnline = () => void run(true);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    window.addEventListener("focus", refreshWhenFocused);
    window.addEventListener("online", refreshWhenOnline);
    const timer = intervalMs
      ? window.setInterval(() => void run(), intervalMs)
      : undefined;
    return () => {
      document.removeEventListener("visibilitychange", refreshWhenVisible);
      window.removeEventListener("focus", refreshWhenFocused);
      window.removeEventListener("online", refreshWhenOnline);
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [intervalMs, run]);

  return useCallback(() => run(true), [run]);
}
