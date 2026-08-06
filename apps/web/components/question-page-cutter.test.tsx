import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { QuestionPageCutter } from "./question-page-cutter";

const mocks = vi.hoisted(() => ({
  pagePreview: vi.fn(),
  cutQuestion: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  assignmentsApi: mocks,
}));

const page = {
  id: "page-1",
  stored_file_id: "file-1",
  page_number: 1,
  source_page_number: 1,
  width: 100,
  height: 200,
  rotation: 90 as const,
  status: "ready",
};

const question = {
  id: "question-1",
  question_number: "1",
  display_order: 1,
  question_type: "calculation",
  max_score: "10",
  knowledge_points: [],
  regions: [],
};

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  mocks.pagePreview.mockResolvedValue({
    url: "/rotated.png",
    width: 200,
    height: 100,
    rotation: 90,
  });
  mocks.cutQuestion.mockImplementation(
    async (_assignmentId, _pageId, payload) => ({
      ...question,
      id: "question-new",
      question_number: "2",
      regions: [{ id: "region-new", source: "manual", ...payload.region }],
    }),
  );
  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
    configurable: true,
    value: vi.fn(),
  });
  Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
    configurable: true,
    value: vi.fn(() => true),
  });
  Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
    configurable: true,
    value: vi.fn(),
  });
});

it("stays disabled until explicitly started and disables again after saving", async () => {
  const onSaved = vi.fn();
  render(
    <QuestionPageCutter
      assignmentId="assignment-1"
      page={page}
      questions={[question]}
      selectedQuestionId={question.id}
      onSaved={onSaved}
    />,
  );

  expect(
    screen.getByText(
      "默认关闭；需要补题或修正自动识别时再启动，不会自动写入题目。",
    ),
  ).toBeVisible();
  expect(mocks.pagePreview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "开始手动切题" }));
  await screen.findByAltText("第 1 页切题预览");
  expect(mocks.pagePreview).toHaveBeenCalledWith("assignment-1", "page-1");

  const canvas = screen.getByLabelText("题目框选画布");
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: 1000,
    bottom: 1000,
    width: 1000,
    height: 1000,
    toJSON: () => ({}),
  });
  fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 100, clientY: 200 });
  fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 500, clientY: 500 });
  fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 500, clientY: 500 });
  fireEvent.click(screen.getByRole("button", { name: "保存框选区域" }));

  await waitFor(() => expect(mocks.cutQuestion).toHaveBeenCalledTimes(1));
  expect(mocks.cutQuestion).toHaveBeenCalledWith("assignment-1", "page-1", {
    question_id: "question-1",
    region: {
      paper_page_id: "page-1",
      x: 0.2,
      y: 0.5,
      width: 0.3,
      height: 0.4,
      region_type: "question",
    },
  });
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: "开始手动切题" })).toBeVisible();
});

it("creates a new question atomically and reports backend overlap errors", async () => {
  const onSaved = vi.fn();
  mocks.cutQuestion.mockRejectedValueOnce(
    new Error("该区域与已有题目高度重叠"),
  );
  render(
    <QuestionPageCutter
      assignmentId="assignment-1"
      page={{ ...page, rotation: 0 }}
      questions={[]}
      onSaved={onSaved}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "开始手动切题" }));
  const canvas = await screen.findByLabelText("题目框选画布");
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: 100,
    bottom: 100,
    width: 100,
    height: 100,
    toJSON: () => ({}),
  });
  fireEvent.change(screen.getByLabelText("新题题号"), {
    target: { value: "2" },
  });
  fireEvent.change(screen.getByLabelText("新题分值"), {
    target: { value: "5" },
  });
  fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 10, clientY: 10 });
  fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 80, clientY: 40 });
  fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 80, clientY: 40 });
  fireEvent.click(screen.getByRole("button", { name: "保存框选区域" }));
  expect(await screen.findByText("题目区域保存失败")).toBeVisible();
  expect(onSaved).not.toHaveBeenCalled();
});
