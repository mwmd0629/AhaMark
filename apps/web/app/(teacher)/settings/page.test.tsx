import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui";
import SettingsPage from "./page";

const preferences = {
  profile: {
    username: "teacher01",
    display_name: "王老师",
    email: "teacher01@ahamark.local",
  },
  preferences: {
    default_class_id: null,
    rubric_status_filter: "all",
    rubric_page_size: 20,
    compact_rubric_cards: false,
  },
  revision: 2,
  updated_at: null,
  server_managed: {
    external_ai_enabled: false,
    ai_configuration_editable: false,
  },
};

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    json: async () => data,
  } as Response;
}

describe("teacher settings page", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loads and persists account preferences without exposing AI secrets", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/auth/preferences") && init?.method === "PUT") {
          const payload = JSON.parse(String(init.body));
          return jsonResponse({
            ...preferences,
            profile: {
              ...preferences.profile,
              display_name: payload.display_name,
            },
            preferences: payload.preferences,
            revision: 3,
            updated_at: "2026-08-19T08:00:00Z",
          });
        }
        if (url.endsWith("/auth/preferences")) return jsonResponse(preferences);
        if (url.includes("/api/classes?")) {
          return jsonResponse({
            items: [
              {
                id: "class-1",
                name: "高一一班",
                status: "active",
                student_count: 30,
                active_student_count: 30,
                group_count: 0,
                created_at: "2026-08-19T08:00:00Z",
                updated_at: "2026-08-19T08:00:00Z",
              },
            ],
            page: 1,
            page_size: 100,
            total: 1,
            pages: 1,
          });
        }
        if (url.endsWith("/health")) {
          return jsonResponse({
            status: "ok",
            service: "api",
            version: "test",
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ToastProvider>
        <SettingsPage />
      </ToastProvider>,
    );

    const displayName = await screen.findByLabelText(/姓名/);
    expect(displayName).toHaveValue("王老师");
    expect(screen.getByLabelText("用户名")).toHaveValue("teacher01");
    expect(screen.getByLabelText("邮箱")).toHaveValue(
      "teacher01@ahamark.local",
    );
    expect(screen.getByText(/外部 AI 请求：/)).toHaveTextContent("已关闭");
    expect(
      screen.queryByLabelText(/API 密钥|模型|AI 开关/),
    ).not.toBeInTheDocument();

    fireEvent.change(displayName, { target: { value: "王老师（数学）" } });
    fireEvent.change(screen.getByLabelText("默认班级"), {
      target: { value: "class-1" },
    });
    fireEvent.change(screen.getByLabelText("默认模板状态"), {
      target: { value: "draft" },
    });
    fireEvent.change(screen.getByLabelText("每页模板数"), {
      target: { value: "50" },
    });
    fireEvent.click(screen.getByLabelText("使用紧凑模板卡片"));
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/auth/preferences") && init?.method === "PUT",
        ),
      ).toBe(true);
    });
    const saveCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/auth/preferences") && init?.method === "PUT",
    );
    const payload = JSON.parse(String(saveCall?.[1]?.body));
    expect(payload).toEqual({
      expected_revision: 2,
      display_name: "王老师（数学）",
      preferences: {
        default_class_id: "class-1",
        rubric_status_filter: "draft",
        rubric_page_size: 50,
        compact_rubric_cards: true,
      },
    });
    expect(JSON.stringify(payload)).not.toMatch(/api.?key|secret|external_ai/i);
    expect(await screen.findByText("设置已保存到当前账户")).toBeInTheDocument();
  });
});
