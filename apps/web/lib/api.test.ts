import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, request } from "./api";

describe("API request errors", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("replaces browser network jargon with a teacher-readable message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    await expect(request("/api/test")).rejects.toThrow(
      "暂时无法连接服务，请稍后重试",
    );
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
