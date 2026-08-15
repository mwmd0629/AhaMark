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
const candidate = {
  id: "question-candidate-1",
  temporary_number: "1",
  question_type: "other",
  content_text: "旧候选文字",
  suggested_score: undefined,
  confidence: "0.9",
  status: "suggested",
  source: "pdf_text:pypdfium2",
  regions: [
    {
      paper_page_id: "page-1",
      x: "0",
      y: "0",
      width: "1",
      height: "1",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  api.providers.mockResolvedValue({
    provider: "fake",
    version: "1",
    available: true,
    can_start: true,
    text_readiness: {
      mode: "ready",
      action_code: "START_AND_REVIEW",
      limitations: [],
    },
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

it("uses the public can-start capability without showing readiness details", async () => {
  api.providers.mockResolvedValue({
    provider: "internal-provider",
    version: "internal-version",
    available: false,
    can_start: false,
    text_readiness: {
      mode: "blocked",
      action_code: "OCR_REQUIRED",
      limitations: ["IMAGE_PAGES_REQUIRE_OCR"],
    },
    demo: false,
    reason: "internal readiness detail",
    formula: { provider: "unavailable", available: false },
  });

  render(
    <RecognitionWorkspace
      assignmentId="assignment-1"
      paperVersionId="paper-1"
    />,
  );

  expect(
    await screen.findByRole("button", { name: "开始识别" }),
  ).toBeDisabled();
  expect(
    screen.queryByText(/internal readiness detail/),
  ).not.toBeInTheDocument();
});

it("allows a PDF-capable assignment to start when OCR itself is unavailable", async () => {
  api.providers.mockResolvedValue({
    provider: "unavailable",
    version: "none",
    available: false,
    can_start: true,
    text_readiness: {
      mode: "pdf_fallback_only",
      action_code: "PDF_TEXT_MAY_REQUIRE_RESCAN_OR_MANUAL",
      limitations: ["SCANNED_PDF_MAY_REQUIRE_OCR", "IMAGE_PAGES_REQUIRE_OCR"],
    },
    demo: false,
    formula: { provider: "unavailable", available: false },
  });

  render(
    <RecognitionWorkspace
      assignmentId="assignment-1"
      paperVersionId="paper-1"
    />,
  );

  expect(await screen.findByRole("button", { name: "开始识别" })).toBeEnabled();
  expect(
    screen.getByText(
      /可尝试读取 PDF 文字；扫描页或图片页可能需要重新扫描或人工录入/,
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/文字识别：暂不可用/)).not.toBeInTheDocument();
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

it("shows concise actions for source conflicts and supplemented regions", async () => {
  api.pages.mockResolvedValue([
    {
      ...page,
      processing_parameters: {
        page_quality: { level: "good", issues: [] },
        math_structure: { risk_codes: [], evidence: [] },
        source_review: {
          source_conflict_count: 2,
          math_symbol_conflict_count: 1,
          missing_region_count: 1,
          source_agreement_ratio: 0.5,
        },
      },
    },
  ]);
  render(
    <RecognitionWorkspace
      assignmentId="assignment-1"
      paperVersionId="paper-1"
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "开始识别" }));
  expect(
    await screen.findByText(/数学符号在不同文字来源中不一致/),
  ).toBeInTheDocument();
  expect(screen.getByText(/补充识别的文字区域/)).toBeInTheDocument();
  expect(screen.queryByText(/source_conflict_count/)).not.toBeInTheDocument();
});

it("replaces internal recognition errors with an actionable message", async () => {
  api.start.mockResolvedValue({
    id: "job-1",
    paper_version_id: "paper-1",
    status: "failed",
    stage: "failed",
    progress: 100,
    provider: "rapidocr",
    error_code: "RECOGNITION_PROVIDER_UNAVAILABLE",
    error_message: "RapidOCR internal dependency detail",
    page_summary: { total: 1, completed: 0, failed: 1, stale: 0 },
  });
  render(
    <RecognitionWorkspace
      assignmentId="assignment-1"
      paperVersionId="paper-1"
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "开始识别" }));
  expect(
    await screen.findByText("当前页面没有可靠文字，请重新扫描或人工录入。"),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/RapidOCR internal dependency detail/),
  ).not.toBeInTheDocument();
  expect(api.candidates).not.toHaveBeenCalled();
  expect(api.formulaRegions).not.toHaveBeenCalled();
  expect(screen.queryByDisplayValue("旧候选文字")).not.toBeInTheDocument();
});

it("clears actionable results immediately when a failed page is retried", async () => {
  api.pages.mockResolvedValue([{ ...page, status: "failed" }]);
  api.candidates.mockResolvedValue([candidate]);
  api.formulaRegions.mockResolvedValue([formula]);
  api.retryPage.mockResolvedValue({
    id: "job-1",
    paper_version_id: "paper-1",
    status: "queued",
    stage: "queued",
    progress: 0,
    provider: "fake",
    page_summary: { total: 1, completed: 0, failed: 0, stale: 0 },
  });
  render(
    <RecognitionWorkspace
      assignmentId="assignment-1"
      paperVersionId="paper-1"
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "开始识别" }));
  expect(await screen.findByDisplayValue("旧候选文字")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "重试" }));

  await waitFor(() =>
    expect(screen.queryByDisplayValue("旧候选文字")).not.toBeInTheDocument(),
  );
  expect(screen.queryByAltText("处理后页面")).not.toBeInTheDocument();
  expect(screen.getByTestId("recognition-job")).toHaveAttribute(
    "data-status",
    "queued",
  );
});

it("shows only actionable public page-quality guidance", async () => {
  api.candidates.mockResolvedValue([candidate]);
  api.pages.mockResolvedValue([
    {
      ...page,
      quality: {
        level: "rescan_required",
        issues: ["blur", "crop_risk"],
      },
      math_structure: {
        risk_codes: [
          "FORMULA_REVIEW_REQUIRED",
          "MATH_LAYOUT_REVIEW_REQUIRED",
          "READING_ORDER_CONFLICT",
        ],
        evidence: [
          { block_indexes: [0], region: [0, 0, 1, 1] },
          { block_indexes: [1], region: [0, 0, 1, 1] },
          { block_indexes: [2], region: [0, 0, 1, 1] },
        ],
      },
      processing_parameters: {
        page_quality: {
          metrics: { laplacian_variance: 12.34 },
        },
        recognition_provider: "internal-provider",
      },
    },
  ]);
  render(
    <RecognitionWorkspace
      assignmentId="assignment-1"
      paperVersionId="paper-1"
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "开始识别" }));
  expect(
    await screen.findByText(/当前页面无法可靠读取，请重新拍摄或扫描后再识别/),
  ).toHaveTextContent("画面模糊、内容可能被裁切");
  expect(screen.getByRole("button", { name: "确认生成题目" })).toBeDisabled();
  expect(screen.queryByText(/laplacian_variance/)).not.toBeInTheDocument();
  expect(screen.queryByText(/internal-provider/)).not.toBeInTheDocument();
  expect(
    screen.getByText(/本页含数学公式，请对照原页核对/),
  ).toBeInTheDocument();
  expect(screen.getByText(/数学排版可能影响含义/)).toBeInTheDocument();
  expect(screen.getByText(/页面疑似多栏或阅读顺序不明确/)).toBeInTheDocument();
  expect(screen.queryByText(/block_indexes/)).not.toBeInTheDocument();

  cleanup();
  api.pages.mockResolvedValue([
    {
      ...page,
      quality: { level: "review_required", issues: ["shadow"] },
    },
  ]);
  render(
    <RecognitionWorkspace
      assignmentId="assignment-1"
      paperVersionId="paper-1"
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "开始识别" }));
  expect(
    await screen.findByText(/当前页面可以继续使用，但请对照原页核对识别内容/),
  ).toHaveTextContent("阴影遮挡");
  expect(screen.getByRole("button", { name: "确认生成题目" })).toBeEnabled();

  cleanup();
  api.pages.mockResolvedValue([
    { ...page, quality: { level: "good", issues: [] } },
  ]);
  render(
    <RecognitionWorkspace
      assignmentId="assignment-1"
      paperVersionId="paper-1"
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "开始识别" }));
  expect(
    await screen.findByText("页面清晰，可继续核对题目。"),
  ).toBeInTheDocument();
});
