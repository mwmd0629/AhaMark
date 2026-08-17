import { act, render, waitFor } from "@testing-library/react";
import { useSmartRefresh } from "./use-smart-refresh";
import { afterEach, expect, it, vi } from "vitest";

function Harness({
  refresh,
  enabled = true,
}: {
  refresh: () => Promise<unknown> | void;
  enabled?: boolean;
}) {
  useSmartRefresh(refresh, {
    enabled,
    intervalMs: 1_000,
    minIntervalMs: 0,
  });
  return null;
}

afterEach(() => {
  vi.useRealTimers();
});

it("refreshes on focus, reconnect and the visible-page interval", async () => {
  vi.useFakeTimers();
  const refresh = vi.fn().mockResolvedValue(undefined);
  render(<Harness refresh={refresh} />);

  window.dispatchEvent(new Event("focus"));
  await act(async () => Promise.resolve());
  expect(refresh).toHaveBeenCalledTimes(1);

  window.dispatchEvent(new Event("online"));
  await act(async () => Promise.resolve());
  expect(refresh).toHaveBeenCalledTimes(2);

  await act(async () => vi.advanceTimersByTimeAsync(1_000));
  expect(refresh).toHaveBeenCalledTimes(3);
});

it("deduplicates overlapping refreshes and runs one queued refresh", async () => {
  let resolveRefresh: (() => void) | undefined;
  const refresh = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveRefresh = resolve;
      }),
  );
  render(<Harness refresh={refresh} />);

  window.dispatchEvent(new Event("online"));
  window.dispatchEvent(new Event("online"));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  resolveRefresh?.();
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2));
  resolveRefresh?.();
});

it("pauses while the page is editing and resumes after editing", async () => {
  const refresh = vi.fn().mockResolvedValue(undefined);
  const view = render(<Harness refresh={refresh} enabled={false} />);

  window.dispatchEvent(new Event("online"));
  await act(async () => Promise.resolve());
  expect(refresh).not.toHaveBeenCalled();

  view.rerender(<Harness refresh={refresh} enabled />);
  window.dispatchEvent(new Event("online"));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
});
