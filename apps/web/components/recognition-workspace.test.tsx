import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RecognitionWorkspace } from "./recognition-workspace";

const api = vi.hoisted(() => ({
  providers: vi.fn(),
  start: vi.fn(),
  job: vi.fn(),
  pages: vi.fn(),
  candidates: vi.fn(),
  retryPage: vi.fn(),
  patchCandidate: vi.fn(),
  confirm: vi.fn(),
  formulaRegions: vi.fn(),
  createFormulaRegion: vi.fn(),
  updateFormulaRegion: vi.fn(),
  recognizeFormula: vi.fn(),
  disposeFormulaCandidate: vi.fn(),
  markFormulaUnreadable: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  recognitionApi: api,
}));

const page = {
  id: "page-result-1",
  paper_page_id: "page-1",
  status: "completed",
  stage: "completed",
  progress: 100,
  processed_url: "/processed.png",
  rendered_url: "/rendered.png",
};
const formula = {
  id: "formula-1",
  paper_page_id: "page-1",
  region_kind: "unknown" as const,
  region: { x: "0.1", y: "0.1", width: "0.5", height: "0.3" },
  status: "manual_required",
  has_alternatives: true,
  candidates: [
    {
      id: "candidate-1",
      rank: 1,
      latex: "x^2",
      confidence: "0.98",
      warning_codes: [],
      status: "manual_required",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  api.providers.mockResolvedValue({
    provider: "fake",
    version: "1",
    available: true,
    demo: true,
    formula: { provider: "fake", available: true },
  });
  api.start.mockResolvedValue({
    id: "job-1",
    paper_version_id: "paper-1",
    status: "completed",
    stage: "completed",
    progress: 100,
    provider: "fake",
    page_summary: { total: 1, completed: 1, failed: 0, stale: 0 },
  });
  api.pages.mockResolvedValue([page]);
  api.candidates.mockResolvedValue([]);
  api.formulaRegions.mockResolvedValue([]);
  api.createFormulaRegion.mockResolvedValue({ ...formula, candidates: [] });
  api.updateFormulaRegion.mockResolvedValue({ ...formula, candidates: [] });
  api.recognizeFormula.mockResolvedValue(formula);
  api.disposeFormulaCandidate.mockResolvedValue({
    ...formula,
    status: "confirmed",
    has_alternatives: false,
    candidates: [{ ...formula.candidates[0], status: "accepted" }],
  });
  api.markFormulaUnreadable.mockResolvedValue({
    ...formula,
    status: "rejected",
    has_alternatives: false,
    candidates: [],
    unreadable_reason: "subscript_ambiguous",
  });
});

it("offers a concise redraw or explicitly confirmed unreadable path", async () => {
  render(
    <RecognitionWorkspace
      assignmentId="assignment-1"
      paperVersionId="paper-1"
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "开始识别" }));
  const canvas = await screen.findByAltText("处理后页面");
  Object.defineProperty(canvas.parentElement!, "getBoundingClientRect", {
    value: () => ({ left: 0, top: 0, width: 1000, height: 1000 }),
  });
  fireEvent.click(screen.getByRole("button", { name: "框选公式" }));
  fireEvent.pointerDown(canvas.parentElement!, {
    clientX: 100,
    clientY: 100,
    pointerId: 1,
  });
  fireEvent.pointerUp(canvas.parentElement!, {
    clientX: 600,
    clientY: 400,
    pointerId: 1,
  });
  fireEvent.click(await screen.findByRole("button", { name: "识别公式" }));
  await screen.findByDisplayValue("x^2");

  fireEvent.click(screen.getByRole("button", { name: "标记无法识别" }));
  fireEvent.change(screen.getByLabelText("原因"), {
    target: { value: "subscript_ambiguous" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认标记" }));
  await waitFor(() =>
    expect(api.markFormulaUnreadable).toHaveBeenCalledWith(
      "assignment-1",
      "job-1",
      "formula-1",
      "subscript_ambiguous",
    ),
  );
  expect(
    await screen.findByText("已标记为无法可靠识别，不会采用识别结果。"),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "重新框选" }));
  expect(screen.getByText(/重新框选这条公式/)).toBeInTheDocument();
  fireEvent.pointerDown(canvas.parentElement!, {
    clientX: 200,
    clientY: 200,
    pointerId: 2,
  });
  fireEvent.pointerUp(canvas.parentElement!, {
    clientX: 700,
    clientY: 500,
    pointerId: 2,
  });
  await waitFor(() =>
    expect(api.updateFormulaRegion).toHaveBeenCalledWith(
      "assignment-1",
      "job-1",
      "formula-1",
      {
        region_kind: "unknown",
        x: 0.2,
        y: 0.2,
        width: 0.5,
        height: 0.3,
      },
    ),
  );
});
afterEach(cleanup);

it("requires explicit formula drawing, shows the top result and confirms teacher edits", async () => {
  render(
    <RecognitionWorkspace
      assignmentId="assignment-1"
      paperVersionId="paper-1"
    />,
  );
  await waitFor(() => expect(api.providers).toHaveBeenCalled());
  expect(screen.getByTestId("recognition-workspace")).toHaveTextContent(
    "公式识别：可用",
  );
  fireEvent.click(screen.getByRole("button", { name: "开始识别" }));
  await screen.findByAltText("处理后页面");

  const canvas = screen.getByAltText("处理后页面").parentElement!;
  Object.defineProperty(canvas, "getBoundingClientRect", {
    value: () => ({ left: 0, top: 0, width: 1000, height: 1000 }),
  });
  fireEvent.click(screen.getByRole("button", { name: "框选公式" }));
  fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100, pointerId: 1 });
  fireEvent.pointerMove(canvas, { clientX: 600, clientY: 400, pointerId: 1 });
  fireEvent.pointerUp(canvas, { clientX: 600, clientY: 400, pointerId: 1 });
  await waitFor(() => expect(api.createFormulaRegion).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: "识别公式" }));
  await screen.findByDisplayValue("x^2");
  expect(screen.getByText("查看其他结果")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("公式"), {
    target: { value: "x^2+1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认公式" }));
  await waitFor(() =>
    expect(api.disposeFormulaCandidate).toHaveBeenCalledWith(
      "assignment-1",
      "job-1",
      "formula-1",
      "candidate-1",
      {
        action: "accept",
        explicit_confirmation: true,
        edited_latex: "x^2+1",
      },
    ),
  );
});
