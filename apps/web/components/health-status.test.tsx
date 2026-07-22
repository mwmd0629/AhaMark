import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HealthStatus } from "./health-status";
afterEach(() => vi.restoreAllMocks());
describe("HealthStatus", () => {
  it("shows connected state", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue({ ok: true, json: async () => ({ status: "ok" }) }),
    );
    render(<HealthStatus />);
    expect(screen.getByText("正在连接后端…")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("后端服务已连接")).toBeInTheDocument(),
    );
  });
  it("shows unavailable state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<HealthStatus />);
    await waitFor(() =>
      expect(screen.getByText(/后端暂不可用/)).toBeInTheDocument(),
    );
  });
});
