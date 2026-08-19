import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, request } from "./api";

describe("API request errors", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("normalizes a fetch rejection into a stable network error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    const error = await request("/api/test").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 0,
      message: "无法连接服务器，请检查网络或确认后端服务已启动。",
      body: {
        code: "NETWORK_ERROR",
        details: {},
        request_id: "",
      },
    });
  });

  it("preserves an intentional request abort", async () => {
    const aborted = new DOMException("Aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(aborted));

    await expect(request("/api/test")).rejects.toBe(aborted);
  });

  it("keeps a readable error when the server response is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        headers: new Headers(),
        json: vi.fn().mockRejectedValue(new SyntaxError("not json")),
      }),
    );

    const error = await request("/api/test").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("请求失败（500），请稍后重试");
  });
});
