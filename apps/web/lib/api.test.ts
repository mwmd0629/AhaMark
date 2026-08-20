import { afterEach, expect, it, vi } from "vitest";
import { ApiError, authApi, request } from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());

it("normalizes FastAPI validation details into a visible API error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [
            {
              loc: ["body", "external_url"],
              msg: "external_url 仅支持 HTTPS",
            },
          ],
        }),
        { status: 422, headers: { "x-request-id": "request-validation" } },
      ),
    ),
  );

  const failure = await request("/api/test").catch((reason: unknown) => reason);
  expect(failure).toBeInstanceOf(ApiError);
  expect(failure).toMatchObject({
    status: 422,
    message: "body.external_url：external_url 仅支持 HTTPS",
    body: {
      code: "HTTP_422",
      request_id: "request-validation",
    },
  });
});

it("falls back safely when an upstream error is not JSON", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response("bad gateway", {
        status: 502,
        statusText: "Bad Gateway",
        headers: { "x-request-id": "request-proxy" },
      }),
    ),
  );

  const failure = await request("/api/test").catch((reason: unknown) => reason);
  expect(failure).toMatchObject({
    status: 502,
    message: "Bad Gateway",
    body: { code: "HTTP_502", request_id: "request-proxy" },
  });
});

it("normalizes a fetch rejection into a stable network error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
  );

  const failure = await request("/api/test").catch((reason: unknown) => reason);
  expect(failure).toBeInstanceOf(ApiError);
  expect(failure).toMatchObject({
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

it("uses login identifiers and recovery-email payloads", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: "student-user",
          email: "student@example.com",
          login_name: "20260001",
          recovery_email_verified: false,
          display_name: "学生甲",
          must_change_password: false,
          roles: ["student"],
          active_student_link: true,
          landing_surface: "student",
        }),
        { status: 200 },
      ),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          challenge_id: "challenge-1",
          message: "若账号与安全邮箱匹配，验证码将发送到该邮箱",
          expires_in_seconds: 600,
          development_code: null,
        }),
        { status: 202 },
      ),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: "student-user",
          email: "new@example.com",
          login_name: "20260001",
          recovery_email_verified: false,
          display_name: "学生甲",
          must_change_password: false,
          roles: ["student"],
          active_student_link: true,
          landing_surface: "student",
        }),
        { status: 200 },
      ),
    );
  vi.stubGlobal("fetch", fetchMock);

  await authApi.login("20260001", "password123");
  await authApi.requestPasswordReset("20260001", "student@example.com");
  await authApi.updateRecoveryEmail("new@example.com", "password123");

  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
    identifier: "20260001",
    password: "password123",
  });
  expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
    identifier: "20260001",
    recovery_email: "student@example.com",
  });
  expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({
    recovery_email: "new@example.com",
    current_password: "password123",
  });
  expect(fetchMock.mock.calls[2][0]).toContain("/auth/recovery-email");
  expect(fetchMock.mock.calls[2][1]?.method).toBe("PUT");
});
