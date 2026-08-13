import { afterEach, expect, it, vi } from "vitest";
import { ApiError, request } from "@/lib/api";

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
