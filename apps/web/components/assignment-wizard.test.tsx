import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssignmentWizard } from "./assignment-wizard";
import { assignmentsApi } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const question = (id: string, number: string) => ({
    id,
    question_number: number,
    display_order: Number(number),
    question_type: "proof",
    max_score: "10.00",
    content_text: `第 ${number} 题内容`,
    difficulty: "medium" as const,
    knowledge_points: [{ id: `kp-${number}`, name: `知识点 ${number}` }],
    regions: [],
  });
  const assignment = {
    id: "assignment-1",
    title: "线代期末",
    subject: "线性代数",
    grade: "大二",
    status: "draft" as const,
    total_score: "20.00",
    updated_at: "2026-07-25T00:00:00Z",
    classes: [{ id: "class-1", name: "线代 2 班", status: "active" as const }],
    due_at: null,
    completeness: { ready: true, next_step: 5, issues: [] },
    paper_version: {
      id: "paper-1",
      version: 1,
      status: "draft",
      pages: [1, 2, 3].map((page) => ({
        id: `page-${page}`,
        stored_file_id: "file-1",
        file_name: "原试卷.pdf",
        page_number: page,
        source_page_number: page,
        rotation: 0 as const,
        status: "ready",
      })),
      questions: [question("q1", "1"), question("q2", "2")],
    },
  };
  return {
    ...actual,
    classesApi: {
      ...actual.classesApi,
      list: vi.fn().mockResolvedValue({
        items: [
          {
            id: "class-1",
            name: "线代 2 班",
            subject: "线性代数",
            academic_year: "2026-2027",
            semester: "秋季",
            status: "active",
            student_count: 30,
            active_student_count: 30,
            group_count: 0,
            created_at: "2026-07-01T00:00:00Z",
            updated_at: "2026-07-01T00:00:00Z",
          },
          {
            id: "class-2",
            name: "线代 3 班",
            subject: "线性代数",
            academic_year: "2026-2027",
            semester: "秋季",
            status: "active",
            student_count: 28,
            active_student_count: 28,
            group_count: 0,
            created_at: "2026-07-01T00:00:00Z",
            updated_at: "2026-07-01T00:00:00Z",
          },
        ],
      }),
    },
    assignmentGenerationApi: {
      ...actual.assignmentGenerationApi,
      capabilities: vi.fn().mockResolvedValue({
        enabled: true,
        provider: "unavailable",
        provider_status: "unavailable",
        external_provider_requests: false,
        teacher_start_allowed: true,
        suggestion_only: true,
        real_provider_quality_passed: false,
      }),
      listJobs: vi.fn().mockResolvedValue([]),
      listRevisions: vi.fn().mockResolvedValue([
        {
          id: "revision-1",
          assignment_id: "assignment-1",
          generation_job_id: "job-1",
          revision: 1,
          source_snapshot_hash: "a".repeat(64),
          status: "partial",
          draft_payload: {},
          risk_summary: { info: 0, warning: 0, blocking: 0 },
          teacher_edit_version: 0,
          created_at: "2026-07-25T00:00:00Z",
          updated_at: "2026-07-25T00:00:00Z",
        },
      ]),
      listFieldSuggestions: vi.fn().mockResolvedValue([
        {
          id: "subject-suggestion",
          field_name: "subject",
          suggested_value: "数学",
          normalized_value: "数学",
          confidence: 0.85,
          evidence: [],
          suggestion_version: 1,
          status: "suggested",
          teacher_edit_version: 0,
        },
        {
          id: "title-suggestion",
          field_name: "title",
          suggested_value: "合成课程-数学分析-第四周",
          normalized_value: "合成课程-数学分析-第四周",
          confidence: 0.82,
          evidence: [],
          suggestion_version: 1,
          status: "suggested",
          teacher_edit_version: 0,
        },
        {
          id: "score-suggestion",
          field_name: "total_score",
          suggested_value: "100",
          normalized_value: "100",
          confidence: 0.8,
          evidence: [],
          suggestion_version: 1,
          status: "suggested",
          teacher_edit_version: 0,
        },
      ]),
      listFileAnalyses: vi.fn().mockResolvedValue([]),
      listTextbookLibraries: vi.fn().mockResolvedValue([]),
      listTextbookLibrarySelections: vi.fn().mockResolvedValue([]),
    },
    assignmentsApi: {
      ...actual.assignmentsApi,
      get: vi.fn().mockResolvedValue(assignment),
      update: vi.fn().mockImplementation(async (_id, data) => ({
        ...assignment,
        ...data,
        updated_at: "2026-07-25T01:00:00Z",
      })),
      setClasses: vi.fn().mockResolvedValue(assignment),
      upload: vi.fn().mockResolvedValue({
        id: "file-2",
        name: "新试卷.pdf",
        pages_created: 3,
      }),
      removeFile: vi.fn().mockResolvedValue({
        id: "file-1",
        pages_deleted: 3,
      }),
      preview: vi.fn().mockResolvedValue({
        url: "https://example.test/paper.pdf?signature=1",
      }),
      pagePreview: vi
        .fn()
        .mockImplementation(async (_assignmentId, pageId) => ({
          url: `https://example.test/${pageId}.png?signature=1`,
          width: 1200,
          height: 1600,
          rotation: 0,
        })),
      availableClassResources: vi.fn().mockResolvedValue([
        {
          id: "resource-1",
          title: "矩阵习题",
          file_name: "矩阵习题.pdf",
          resource_type: "exercise",
          page_count: 3,
          status: "ready",
          created_at: "2026-08-12T00:00:00Z",
        },
      ]),
      addClassResources: vi.fn().mockResolvedValue({
        files_created: 1,
        pages_created: 3,
      }),
      page: vi.fn().mockResolvedValue({}),
      question: vi.fn().mockImplementation(async (_assignmentId, data) => ({
        ...question("created-question", data.question_number),
        ...data,
        max_score: Number(data.max_score).toFixed(2),
        knowledge_points: data.knowledge_points.map(
          (name: string, index: number) => ({
            id: `created-kp-${index}`,
            name,
          }),
        ),
      })),
      updateQuestion: vi
        .fn()
        .mockImplementation(async (_assignmentId, id, data) => ({
          ...question(id, data.question_number),
          ...data,
          max_score: Number(data.max_score).toFixed(2),
          knowledge_points: data.knowledge_points.map(
            (name: string, index: number) => ({
              id: `updated-kp-${index}`,
              name,
            }),
          ),
        })),
      region: vi.fn().mockResolvedValue({}),
    },
  };
});

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("三步向导把基本信息和上传集中在准备作业", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" initialStep={1} />);

  expect(
    await screen.findByRole("button", { name: "上传试卷文件" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: /作业名称/ })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /步骤 1.*准备作业/ }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /步骤 2.*核对内容/ }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /步骤 3.*确认发布/ }),
  ).toBeInTheDocument();
  expect(screen.queryByText("添加题目")).not.toBeInTheDocument();
});

it("显示并保存练习草稿的作答要求或复习范围", async () => {
  const current = await assignmentsApi.get("assignment-1");
  vi.mocked(assignmentsApi.get).mockResolvedValueOnce({
    ...current,
    instructions: "1. 函数单元测验 · 第3题 · 知识点：函数单调性",
  });
  render(<AssignmentWizard assignmentId="assignment-1" initialStep={1} />);

  const instructions = await screen.findByRole("textbox", {
    name: "作答要求或复习范围",
  });
  expect(instructions).toHaveValue(
    "1. 函数单元测验 · 第3题 · 知识点：函数单调性",
  );
  await screen.findByText("已选择 1 个班级");
  fireEvent.change(instructions, {
    target: { value: "重点复习函数单调性，并完成教师确认后的练习题。" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));

  await waitFor(() =>
    expect(assignmentsApi.update).toHaveBeenCalledWith(
      "assignment-1",
      expect.objectContaining({
        instructions: "重点复习函数单调性，并完成教师确认后的练习题。",
      }),
      current.updated_at,
    ),
  );
});

it("面向大学课程显示可采用的名称建议且不覆盖已确认总分", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" initialStep={1} />);

  await screen.findByText("大学课程");
  await waitFor(() =>
    expect(document.querySelector('input[name="subject"]')).toHaveValue(
      "线性代数",
    ),
  );
  expect(document.querySelector('input[name="total_score"]')).toBeRequired();
  expect(
    screen.queryByText("请填写具体大学课程，不使用笼统的“数学”。"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(
      "上传后在下方逐个确认“题目”或“答案”，未经确认不会开始抽取题目。",
    ),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("AI 生成")).not.toBeInTheDocument();
  const suggestion = screen.getByRole("button", {
    name: /^线性代数 .+ 作业$/,
  });
  const titleField = screen.getByTestId("assignment-title-field");
  expect(titleField).toContainElement(screen.getByLabelText("作业名称"));
  expect(titleField).toContainElement(suggestion);
  expect(suggestion).toHaveClass("text-xs");
  expect(screen.getByLabelText("作业名称")).toHaveValue("线代期末");
  fireEvent.click(suggestion);
  expect((screen.getByLabelText("作业名称") as HTMLInputElement).value).toMatch(
    /^线性代数 .+ 作业$/,
  );
  const subjectSuggestion = await screen.findByRole("button", {
    name: "数学分析",
  });
  const subjectField = screen.getByTestId("assignment-subject-field");
  const scoreField = screen.getByTestId("assignment-total-score-field");
  expect(subjectField).toContainElement(screen.getByLabelText("大学课程"));
  expect(subjectField).toContainElement(subjectSuggestion);
  expect(scoreField).toContainElement(screen.getByLabelText("总分"));
  expect(screen.queryByRole("button", { name: "100" })).not.toBeInTheDocument();
  expect(subjectSuggestion).toHaveClass("text-xs");
  fireEvent.click(subjectSuggestion);
  expect(screen.getByLabelText("大学课程")).toHaveValue("数学分析");
  expect(screen.getByLabelText("总分")).toHaveValue(20);
  expect(assignmentsApi.update).not.toHaveBeenCalled();
});

it("发布班级默认收起并支持搜索、全选当前结果和清空", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" initialStep={1} />);

  expect(await screen.findByText("已选择 1 个班级")).toBeInTheDocument();
  expect(
    screen.queryByRole("checkbox", { name: /线代 3 班/ }),
  ).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "选择发布班级" }));
  fireEvent.change(screen.getByRole("textbox", { name: "搜索班级" }), {
    target: { value: "3 班" },
  });
  expect(
    screen.getByText("显示 1 / 2 个班级，已选班级置顶"),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "全选当前结果" }));
  expect(screen.getByText("已选择 2 个班级")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "清空" }));
  expect(screen.getByText("尚未选择发布班级")).toBeInTheDocument();
});

it("核对内容回填所选题目的题号、分值、知识点和内容并可保存", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" initialStep={2} />);

  expect(await screen.findByDisplayValue("1")).toBeInTheDocument();
  expect(screen.getByDisplayValue("10.00")).toBeInTheDocument();
  expect(screen.getByDisplayValue("知识点 1")).toBeInTheDocument();
  expect(screen.getByDisplayValue("第 1 题内容")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("知识点（逗号分隔）"), {
    target: { value: "矩阵加法, 矩阵乘法" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存题目" }));

  await waitFor(() =>
    expect(assignmentsApi.updateQuestion).toHaveBeenCalledWith(
      "assignment-1",
      "q1",
      expect.objectContaining({
        question_number: "1",
        max_score: 10,
        knowledge_points: ["矩阵加法", "矩阵乘法"],
      }),
    ),
  );
});

it("整理页面默认折叠且不再显示手工坐标输入", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" initialStep={2} />);

  const organizer = await screen.findByText("整理页面（3 页）");
  expect(organizer.closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByLabelText("x")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("y")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("width")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("height")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "保存区域" }),
  ).not.toBeInTheDocument();
});

it("刷新题目数据时保留当前选择和未保存编辑", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" initialStep={2} />);

  fireEvent.click(await screen.findByRole("button", { name: /第 2 题/ }));
  await waitFor(() =>
    expect(screen.getByDisplayValue("第 2 题内容")).toBeInTheDocument(),
  );
  fireEvent.change(screen.getByLabelText("题目内容"), {
    target: { value: "第 2 题未保存修改" },
  });
  fireEvent.click(screen.getByRole("button", { name: "旋转 90°" }));

  await waitFor(() => expect(assignmentsApi.page).toHaveBeenCalled());
  expect(screen.getByDisplayValue("第 2 题未保存修改")).toBeInTheDocument();
  expect(screen.getByText("编辑第 2 题")).toBeInTheDocument();
});

it("脏编辑对应题目被后台移除时阻止误写其他题目", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" initialStep={2} />);

  fireEvent.click(await screen.findByRole("button", { name: /第 2 题/ }));
  await waitFor(() =>
    expect(screen.getByDisplayValue("第 2 题内容")).toBeInTheDocument(),
  );
  fireEvent.change(screen.getByLabelText("题目内容"), {
    target: { value: "不能覆盖第 1 题的本地修改" },
  });
  const current = await assignmentsApi.get("assignment-1");
  if (!current.paper_version) throw new Error("paper version missing");
  vi.mocked(assignmentsApi.get).mockResolvedValueOnce({
    ...current,
    paper_version: {
      ...current.paper_version,
      questions: current.paper_version.questions.filter(
        (question) => question.id !== "q2",
      ),
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "旋转 90°" }));

  expect(
    await screen.findByText("当前题目已被后台更新或移除"),
  ).toBeInTheDocument();
  expect(
    screen.getByDisplayValue("不能覆盖第 1 题的本地修改"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "添加题目" })).toBeDisabled();
  expect(assignmentsApi.updateQuestion).not.toHaveBeenCalled();
});

it("题目提交期间禁用按钮并阻止重复创建", async () => {
  vi.mocked(assignmentsApi.question).mockImplementationOnce(
    () => new Promise(() => undefined),
  );
  render(<AssignmentWizard assignmentId="assignment-1" initialStep={2} />);

  fireEvent.click(await screen.findByRole("button", { name: "新增题目" }));
  fireEvent.change(screen.getByLabelText("题号"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("分值"), { target: { value: "10" } });
  const submit = screen.getByRole("button", { name: "添加题目" });
  fireEvent.click(submit);
  fireEvent.click(submit);

  expect(assignmentsApi.question).toHaveBeenCalledTimes(1);
  expect(submit).toBeDisabled();
});

it("支持拖拽上传且成功时只安静保留文件", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByRole("heading", { name: "线代期末" });
  fireEvent.click(screen.getByRole("button", { name: /准备作业/ }));
  expect(screen.getByRole("status")).toHaveTextContent(
    "你现在在：第 1 步 · 准备作业",
  );
  const file = new File(["paper"], "新试卷.pdf", {
    type: "application/pdf",
  });
  fireEvent.drop(screen.getByRole("button", { name: "上传试卷文件" }), {
    dataTransfer: { files: [file] },
  });
  await waitFor(() =>
    expect(assignmentsApi.upload).toHaveBeenCalledWith("assignment-1", file),
  );
  expect(screen.queryByText("上传成功")).not.toBeInTheDocument();
  expect(screen.queryByText(/共 3 页/)).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "已上传文件" })).toHaveTextContent(
    "原试卷.pdf",
  );
  expect(screen.queryByText("已保留")).not.toBeInTheDocument();
  expect(
    screen.queryByText("继续添加不会删除已有文件"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "进入内容核对" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "删除 原试卷.pdf" })).toHaveClass(
    "text-red-600",
  );
  expect(
    screen.queryByRole("button", { name: "继续添加文件" }),
  ).not.toBeInTheDocument();
});

it("一次选择多个文件后按顺序自动上传", async () => {
  vi.mocked(assignmentsApi.upload)
    .mockResolvedValueOnce({
      id: "file-2",
      name: "试卷上册.pdf",
      pages_created: 2,
    })
    .mockResolvedValueOnce({
      id: "file-3",
      name: "试卷下册.pdf",
      pages_created: 3,
    });
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByRole("heading", { name: "线代期末" });
  fireEvent.click(screen.getByRole("button", { name: /准备作业/ }));
  const first = new File(["first"], "试卷上册.pdf", {
    type: "application/pdf",
  });
  const second = new File(["second"], "试卷下册.pdf", {
    type: "application/pdf",
  });

  fireEvent.change(screen.getByLabelText("选择试卷文件"), {
    target: { files: [first, second] },
  });

  await waitFor(() =>
    expect(assignmentsApi.upload).toHaveBeenNthCalledWith(
      1,
      "assignment-1",
      first,
    ),
  );
  expect(screen.queryByText("2 个文件")).not.toBeInTheDocument();
  expect(screen.queryByText("上传成功")).not.toBeInTheDocument();
  expect(screen.queryByText(/共 5 页/)).not.toBeInTheDocument();
  expect(assignmentsApi.upload).toHaveBeenNthCalledWith(
    2,
    "assignment-1",
    second,
  );
});

it("可以从班级资料选择文件并复制到当前作业", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" initialStep={1} />);

  expect(await screen.findByText("从班级资料选择")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: /矩阵习题/ }));
  fireEvent.click(screen.getByRole("button", { name: "加入所选资料" }));

  await waitFor(() =>
    expect(assignmentsApi.addClassResources).toHaveBeenCalledWith(
      "assignment-1",
      ["resource-1"],
    ),
  );
});

it("确认后删除已上传文件", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByRole("heading", { name: "线代期末" });
  fireEvent.click(screen.getByRole("button", { name: /准备作业/ }));
  fireEvent.click(screen.getByRole("button", { name: "删除 原试卷.pdf" }));
  await waitFor(() =>
    expect(assignmentsApi.removeFile).toHaveBeenCalledWith(
      "assignment-1",
      "file-1",
    ),
  );
  expect(window.confirm).toHaveBeenCalledWith(
    "确定删除“原试卷.pdf”吗？其对应页面也会删除。",
  );
  confirm.mockRestore();
});

it("拒绝非法格式并允许重新选择", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByRole("heading", { name: "线代期末" });
  fireEvent.click(screen.getByRole("button", { name: /准备作业/ }));
  fireEvent.drop(screen.getByRole("button", { name: "上传试卷文件" }), {
    dataTransfer: {
      files: [
        new File(["bad"], "脚本.exe", { type: "application/octet-stream" }),
      ],
    },
  });
  expect(
    screen.getByText("脚本.exe 格式不支持，请选择 PDF、PNG 或 JPG 文件。"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新上传" })).toBeInTheDocument();
});

it("切换缩略图时同步当前页面高亮和大图", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByRole("heading", { name: "线代期末" });
  fireEvent.click(screen.getByRole("button", { name: /核对内容/ }));
  const thumbnailStrip = await screen.findByTestId(
    "assignment-page-thumbnails",
  );
  expect(thumbnailStrip).toHaveClass("flex", "overflow-x-auto");
  const page2 = await screen.findByRole("button", { name: /第 2 页/ });
  expect(page2).toHaveClass("w-36", "shrink-0");
  fireEvent.click(page2);
  expect(page2).toHaveAttribute("aria-current", "page");
  const preview = await screen.findByTitle("第 2 页大图预览");
  expect(preview).toBeInTheDocument();
  expect(preview.tagName).toBe("IMG");
  expect(preview).toHaveAttribute("src", expect.stringContaining("page-2.png"));
  expect(screen.getByTitle("第 2 页缩略图").tagName).toBe("IMG");
  expect(document.querySelector('iframe[title*="页缩略图"]')).toBeNull();
});

it("无截止时间保存为 null，回显时保持无截止时间", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByRole("heading", { name: "线代期末" });
  fireEvent.click(screen.getByRole("button", { name: /准备作业/ }));
  expect(screen.getByRole("radio", { name: /无截止时间/ })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));
  await waitFor(() =>
    expect(assignmentsApi.update).toHaveBeenCalledWith(
      "assignment-1",
      expect.objectContaining({ due_at: null }),
      "2026-07-25T00:00:00Z",
    ),
  );
});

it("发布前可以调整班级，并将本地截止时间转换为带时区的 ISO 时间", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByRole("heading", { name: "线代期末" });
  fireEvent.click(screen.getByRole("button", { name: /准备作业/ }));
  fireEvent.click(screen.getByRole("button", { name: "选择发布班级" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /线代 3 班/ }));
  fireEvent.click(screen.getByRole("radio", { name: /设置截止时间/ }));
  fireEvent.change(screen.getByLabelText("具体截止时间"), {
    target: { value: "2026-08-01T08:30" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));

  await waitFor(() =>
    expect(assignmentsApi.update).toHaveBeenCalledWith(
      "assignment-1",
      expect.objectContaining({
        due_at: new Date("2026-08-01T08:30").toISOString(),
      }),
      "2026-07-25T00:00:00Z",
    ),
  );
  await waitFor(() =>
    expect(assignmentsApi.setClasses).toHaveBeenCalledWith(
      "assignment-1",
      ["class-1", "class-2"],
      "2026-07-25T01:00:00Z",
    ),
  );
});

it("年级允许大学课程自定义教学层级并回填编辑值", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByRole("heading", { name: "线代期末" });
  fireEvent.click(screen.getByRole("button", { name: /准备作业/ }));

  const grade = screen.getByLabelText("年级或教学层级");
  expect(grade).toHaveValue("大二");
  expect(grade).toHaveAttribute("placeholder", "如：大二、研究生、2026 级");
  expect(grade.tagName).toBe("INPUT");
});
