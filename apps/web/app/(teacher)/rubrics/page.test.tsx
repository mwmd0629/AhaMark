import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui";
import RubricsPage from "./page";

const confirmedRubric = {
  id: "rubric-1",
  question_id: "question-1",
  reference_answer_version_id: "reference-1",
  rubric_version: 1,
  title: "函数单调性评分模板",
  total_points: "5",
  status: "confirmed",
  criteria: [
    {
      stable_key: "final_answer",
      title: "最终答案",
      max_points: "5",
      criterion_type: "final_answer",
      required: true,
      dependencies: [],
      validation_mode: "manual_only",
      validation_rule: {},
    },
  ],
};

const draftRubric = {
  ...confirmedRubric,
  id: "rubric-2",
  rubric_version: 2,
  status: "draft",
};

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
  } as Response;
}

describe("real rubric catalog", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(cleanup);

  it("loads account defaults, filters the catalog, and derives an editable draft", async () => {
    const catalogUrls: string[] = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/auth/preferences")) {
          return jsonResponse({
            profile: { display_name: "王老师", email: "teacher@example.com" },
            preferences: {
              default_class_id: "class-1",
              rubric_status_filter: "all",
              rubric_page_size: 10,
              compact_rubric_cards: true,
            },
            revision: 1,
            updated_at: null,
            server_managed: {
              external_ai_enabled: false,
              ai_configuration_editable: false,
            },
          });
        }
        if (url.includes("/api/classes?")) {
          return jsonResponse({
            items: [{ id: "class-1", name: "高一一班", status: "active" }],
            page: 1,
            page_size: 100,
            total: 1,
            pages: 1,
          });
        }
        if (url.includes("/api/assignments?")) {
          return jsonResponse({
            items: [],
            page: 1,
            page_size: 100,
            total: 0,
            pages: 0,
          });
        }
        if (url.includes("/api/structured-rubrics?")) {
          catalogUrls.push(url);
          return jsonResponse({
            items: [
              {
                rubric: confirmedRubric,
                created_at: "2026-08-19T08:00:00Z",
                confirmed_at: "2026-08-19T09:00:00Z",
                assignment: {
                  id: "assignment-1",
                  title: "函数单元测试",
                  subject: "数学",
                  grade: "高一",
                  status: "published",
                },
                question: {
                  id: "question-1",
                  question_number: "3",
                  content_text: "判断函数的单调性。",
                  max_score: "5",
                },
              },
            ],
            page: 1,
            page_size: 10,
            total: 1,
            pages: 1,
          });
        }
        if (url.endsWith("/api/structured-rubrics/rubric-1/derive")) {
          expect(init?.method).toBe("POST");
          return jsonResponse(draftRubric, 201);
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ToastProvider>
        <RubricsPage />
      </ToastProvider>,
    );

    expect(await screen.findByText("函数单调性评分模板")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes(
          "/api/classes?page=1&page_size=100&status=active&sort=name_asc",
        ),
      ),
    ).toBe(true);
    expect(catalogUrls[0]).toContain("page_size=10");
    expect(catalogUrls[0]).toContain("class_id=class-1");
    expect(
      screen.queryByText(/演示数据|不参与真实评分/),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("模板状态筛选"), {
      target: { value: "confirmed" },
    });
    await waitFor(() =>
      expect(catalogUrls.some((url) => url.includes("status=confirmed"))).toBe(
        true,
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "派生新草稿" }));
    expect(await screen.findByLabelText("Rubric 标题")).toBeEnabled();
    expect(screen.getByText("已派生新的可编辑草稿")).toBeInTheDocument();
  });

  it("creates a real draft from an assignment question with a confirmed answer", async () => {
    const createdBodies: unknown[] = [];
    const assignmentSummary = {
      id: "assignment-1",
      title: "函数单元测试",
      status: "draft",
      updated_at: "2026-08-19T08:00:00Z",
      classes: [],
      completeness: { ready: false, next_step: 1, issues: [] },
    };
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/auth/preferences")) {
          return jsonResponse({
            profile: { display_name: "王老师", email: "teacher@example.com" },
            preferences: {
              default_class_id: null,
              rubric_status_filter: "all",
              rubric_page_size: 20,
              compact_rubric_cards: false,
            },
            revision: 0,
            updated_at: null,
            server_managed: {
              external_ai_enabled: false,
              ai_configuration_editable: false,
            },
          });
        }
        if (url.includes("/api/classes?")) {
          return jsonResponse({
            items: [],
            page: 1,
            page_size: 100,
            total: 0,
            pages: 0,
          });
        }
        if (url.includes("/api/assignments?")) {
          return jsonResponse({
            items: [assignmentSummary],
            page: 1,
            page_size: 100,
            total: 1,
            pages: 1,
          });
        }
        if (url.includes("/api/structured-rubrics?")) {
          return jsonResponse({
            items: [],
            page: 1,
            page_size: 20,
            total: 0,
            pages: 0,
          });
        }
        if (url.endsWith("/api/assignments/assignment-1")) {
          return jsonResponse({
            ...assignmentSummary,
            paper_version: {
              id: "paper-1",
              version: 1,
              status: "confirmed",
              pages: [],
              questions: [
                {
                  id: "question-1",
                  question_number: "3",
                  display_order: 1,
                  question_type: "short_answer",
                  content_text: "判断函数的单调性。",
                  max_score: "5",
                  knowledge_points: [],
                  regions: [],
                },
              ],
            },
          });
        }
        if (url.endsWith("/api/questions/question-1/reference-answers")) {
          if (init?.method === "POST")
            throw new Error("unexpected reference creation");
          return jsonResponse([
            {
              id: "reference-1",
              question_id: "question-1",
              version: 1,
              status: "confirmed",
              source_type: "teacher_authored",
              raw_content: "递增",
              normalized_content: "递增",
              structured_content: {},
              provenance: {},
              content_hash: "hash",
            },
          ]);
        }
        if (url.endsWith("/api/questions/question-1/structured-rubrics")) {
          expect(init?.method).toBe("POST");
          createdBodies.push(JSON.parse(String(init?.body)));
          return jsonResponse(draftRubric, 201);
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ToastProvider>
        <RubricsPage />
      </ToastProvider>,
    );
    await screen.findByText("暂无符合条件的评分模板");
    fireEvent.click(screen.getAllByRole("button", { name: "创建评分模板" })[0]);
    fireEvent.change(screen.getByLabelText("作业"), {
      target: { value: "assignment-1" },
    });
    await waitFor(() => expect(screen.getByLabelText("题目")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("题目"), {
      target: { value: "question-1" },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "创建草稿并编辑" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "创建草稿并编辑" }));

    expect(await screen.findByLabelText("Rubric 标题")).toBeEnabled();
    expect(createdBodies).toHaveLength(1);
    expect(createdBodies[0]).toMatchObject({
      reference_answer_version_id: "reference-1",
      total_points: "5",
      criteria: [{ max_points: "5", validation_mode: "manual_only" }],
    });
  });
});
