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

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const question = (id: string, number: string) => ({
    id,
    question_number: number,
    display_order: Number(number),
    question_type: "proof",
    max_score: "10.00",
    difficulty: "medium" as const,
    knowledge_points: [],
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
    classes: [],
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
      questions: [
        {
          ...question("q1", "1"),
          regions: [
            {
              id: "region-1",
              paper_page_id: "page-1",
              x: "0.1",
              y: "0.15",
              width: "0.8",
              height: "0.2",
              source: "manual" as const,
            },
          ],
        },
        question("q2", "2"),
      ],
    },
    rubric_version: {
      id: "rubric-1",
      version: 1,
      status: "draft",
      question_rubrics: [
        {
          id: "qr1",
          question_id: "q1",
          standard_answer: "第一题已保存答案",
          items: [],
        },
        {
          id: "qr2",
          question_id: "q2",
          standard_answer: "第二题已保存答案",
          items: [],
        },
      ],
    },
  };
  return {
    ...actual,
    classesApi: {
      ...actual.classesApi,
      list: vi.fn().mockResolvedValue({ items: [] }),
    },
    assignmentsApi: {
      ...actual.assignmentsApi,
      get: vi.fn().mockResolvedValue(assignment),
      update: vi.fn().mockImplementation(async (_id, data) => ({
        ...assignment,
        ...data,
        updated_at: "2026-07-25T01:00:00Z",
      })),
      upload: vi.fn().mockResolvedValue({
        id: "file-2",
        name: "新试卷.pdf",
        pages_created: 3,
      }),
      preview: vi.fn().mockResolvedValue({
        url: "https://example.test/paper.pdf?signature=1",
      }),
      pagePreview: vi.fn().mockResolvedValue({
        url: "https://example.test/page.png?signature=1",
        width: 100,
        height: 200,
      }),
      page: vi.fn().mockResolvedValue({}),
      removeQuestion: vi.fn().mockResolvedValue(undefined),
      cutQuestion: vi.fn().mockResolvedValue({
        ...question("q3", "3"),
        max_score: "5.00",
        content_text: "新切分题目",
        regions: [
          {
            id: "region-3",
            paper_page_id: "page-1",
            x: "0.1",
            y: "0.1",
            width: "0.7",
            height: "0.4",
            source: "manual",
          },
        ],
      }),
    },
  };
});

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("回填标准答案并在切换题目时同步更新", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);

  expect(
    await screen.findByDisplayValue("第一题已保存答案"),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Codex 草稿生成/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("当前题目"), {
    target: { value: "q2" },
  });
  await waitFor(() =>
    expect(screen.getByDisplayValue("第二题已保存答案")).toBeInTheDocument(),
  );
});

it("支持拖拽上传并显示文件、处理状态和成功页数", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByDisplayValue("第一题已保存答案");
  fireEvent.click(screen.getByRole("button", { name: /上传与整理页面/ }));
  const file = new File(["paper"], "新试卷.pdf", {
    type: "application/pdf",
  });
  fireEvent.drop(screen.getByRole("button", { name: "上传试卷文件" }), {
    dataTransfer: { files: [file] },
  });
  expect(screen.getByText("新试卷.pdf")).toBeInTheDocument();
  expect(screen.getByText("等待上传")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "开始上传" }));
  expect(await screen.findByText("上传成功")).toBeInTheDocument();
  expect(screen.getAllByText(/共 3 页/).length).toBeGreaterThan(0);
  expect(screen.getByRole("region", { name: "已上传文件" })).toHaveTextContent(
    "原试卷.pdf · 3 页 · 已保留",
  );
  expect(screen.getByText("继续添加不会删除已有文件")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "继续添加文件" }));
  expect(screen.getByRole("region", { name: "已上传文件" })).toHaveTextContent(
    "原试卷.pdf · 3 页 · 已保留",
  );
});

it("拒绝非法格式并允许重新选择", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByDisplayValue("第一题已保存答案");
  fireEvent.click(screen.getByRole("button", { name: /上传与整理页面/ }));
  fireEvent.drop(screen.getByRole("button", { name: "上传试卷文件" }), {
    dataTransfer: {
      files: [
        new File(["bad"], "脚本.exe", { type: "application/octet-stream" }),
      ],
    },
  });
  expect(
    screen.getByText("文件格式不支持，请选择 PDF、PNG 或 JPG 文件。"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新上传" })).toBeInTheDocument();
});

it("切换缩略图时同步当前页面高亮和大图", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByDisplayValue("第一题已保存答案");
  fireEvent.click(screen.getByRole("button", { name: /上传与整理页面/ }));
  const page2 = await screen.findByRole("button", { name: /第 2 页/ });
  fireEvent.click(page2);
  expect(page2).toHaveAttribute("aria-current", "page");
  const preview = await screen.findByAltText("第 2 页切题预览");
  expect(preview).toBeInTheDocument();
  expect(preview).toHaveAttribute("src", expect.stringContaining("page.png"));
});

it("支持在逐页预览上拖拽框选并保存为新题目", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByDisplayValue("第一题已保存答案");
  fireEvent.click(screen.getByRole("button", { name: /上传与整理页面/ }));

  const canvas = await screen.findByRole("application", {
    name: "题目切割画布",
  });
  fireEvent.load(screen.getByAltText("第 1 页切题预览"));
  expect(canvas).toHaveAttribute("aria-disabled", "false");
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: 200,
    bottom: 400,
    width: 200,
    height: 400,
    toJSON: () => ({}),
  });

  fireEvent.pointerDown(canvas, {
    pointerId: 1,
    button: 0,
    isPrimary: true,
    clientX: 20,
    clientY: 40,
  });
  fireEvent.pointerMove(canvas, {
    pointerId: 1,
    clientX: 160,
    clientY: 200,
  });
  fireEvent.pointerUp(canvas, {
    pointerId: 1,
    clientX: 160,
    clientY: 200,
  });
  expect(screen.getByTestId("draft-question-region")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("分值"), {
    target: { value: "5" },
  });
  fireEvent.change(screen.getByLabelText("题目内容（可选）"), {
    target: { value: "新切分题目" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存为新题目" }));

  await waitFor(() =>
    expect(assignmentsApi.cutQuestion).toHaveBeenCalledWith(
      "assignment-1",
      "page-1",
      expect.objectContaining({
        question: expect.objectContaining({
          question_number: "3",
          max_score: 5,
        }),
        region: expect.objectContaining({
          paper_page_id: "page-1",
          x: 0.1,
          y: 0.1,
          width: 0.7,
          height: 0.4,
        }),
      }),
    ),
  );
});

it("页面图片未加载时禁止框选，并可删除误切题目", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByDisplayValue("第一题已保存答案");
  fireEvent.click(screen.getByRole("button", { name: /上传与整理页面/ }));

  const canvas = await screen.findByRole("application", {
    name: "题目切割画布",
  });
  expect(canvas).toHaveAttribute("aria-disabled", "true");
  fireEvent.pointerDown(canvas, {
    pointerId: 2,
    button: 0,
    clientX: 20,
    clientY: 40,
  });
  expect(screen.queryByTestId("draft-question-region")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "删除该题" }));
  await waitFor(() =>
    expect(assignmentsApi.removeQuestion).toHaveBeenCalledWith(
      "assignment-1",
      "q1",
    ),
  );
});

it("只显示精简后的三步向导", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByDisplayValue("第一题已保存答案");

  const navigation = screen.getByRole("list", { name: "创建步骤" });
  expect(navigation).toHaveTextContent("基本信息");
  expect(navigation).toHaveTextContent("上传与整理页面");
  expect(navigation).toHaveTextContent("评分标准");
  expect(navigation).not.toHaveTextContent("编辑题目");
  expect(navigation).not.toHaveTextContent("集中审查与发布");
  expect(screen.queryByText("Codex 草稿生成")).not.toBeInTheDocument();
});

it("无截止时间保存为 null，回显时保持无截止时间", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByDisplayValue("第一题已保存答案");
  fireEvent.click(screen.getByRole("button", { name: /基本信息/ }));
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

it("年级使用大一至大四下拉选项并回填编辑值", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);
  await screen.findByDisplayValue("第一题已保存答案");
  fireEvent.click(screen.getByRole("button", { name: /基本信息/ }));

  const grade = screen.getByLabelText("年级");
  expect(grade).toHaveValue("大二");
  expect(
    Array.from((grade as HTMLSelectElement).options).map(
      (option) => option.value,
    ),
  ).toEqual(["大一", "大二", "大三", "大四"]);
  expect(grade.tagName).toBe("SELECT");
});
