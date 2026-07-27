import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AnswerRecognitionWorkspace } from "./answer-recognition-workspace";

const api = vi.hoisted(() => ({
  blocks: vi.fn(),
  edit: vi.fn(),
  split: vi.fn(),
  merge: vi.fn(),
  reorder: vi.fn(),
  confirm: vi.fn(),
  retry: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ answerRecognitionApi: api }));

const blocks = [
  {
    id: "block-1",
    job_id: "job-1",
    page_id: "page-1",
    region_id: "region-1",
    source_page_number: 1,
    block_type: "text",
    bbox: { x: 0, y: 0, width: 0.5, height: 1 },
    reading_order: 0,
    raw_text: "first answer",
    normalized_text: "first answer",
    confidence: 0.7,
    provider: "fake",
    provider_version: "1",
    warning_codes: ["LOW_CONFIDENCE"],
    requires_review: true,
    status: "requires_review",
    recognition_version: 1,
    stale: false,
    evidence_image_url: "/evidence.png",
  },
  {
    id: "block-2",
    job_id: "job-1",
    page_id: "page-1",
    region_id: "region-1",
    source_page_number: 1,
    block_type: "formula",
    bbox: { x: 0.5, y: 0, width: 0.5, height: 1 },
    reading_order: 1,
    raw_text: "x²",
    normalized_text: "x2",
    latex: "x^2",
    confidence: 0.9,
    provider: "fake",
    provider_version: "1",
    warning_codes: [],
    requires_review: true,
    status: "requires_review",
    recognition_version: 1,
    stale: false,
    evidence_image_url: "/evidence.png",
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.blocks.mockResolvedValue(blocks);
  api.edit.mockResolvedValue(blocks[0]);
  api.split.mockResolvedValue(blocks);
  api.merge.mockResolvedValue(blocks[0]);
  api.reorder.mockResolvedValue({ block_ids: ["block-2", "block-1"] });
  api.confirm.mockResolvedValue({ status: "confirmed" });
  api.retry.mockResolvedValue({
    job_id: "job-1",
    status: "queued",
    generation: 2,
  });
});
afterEach(cleanup);

it("edits, splits, merges, reorders, retries and confirms evidence", async () => {
  render(
    <AnswerRecognitionWorkspace
      submissionId="submission-1"
      answerId="answer-1"
      regionIds={["region-1"]}
    />,
  );
  await screen.findByText("原始：first answer");
  fireEvent.click(screen.getAllByRole("button", { name: "编辑" })[0]);
  const dialog = screen.getByRole("dialog", { name: "编辑识别块" });
  fireEvent.click(dialog);
  expect(dialog).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("规范化文本"), {
    target: { value: "teacher revision" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(api.edit).toHaveBeenCalled());

  fireEvent.click(screen.getAllByRole("button", { name: "拆分" })[0]);
  await waitFor(() => expect(api.split).toHaveBeenCalled());
  const checks = screen.getAllByRole("checkbox");
  fireEvent.click(checks[0]);
  fireEvent.click(checks[1]);
  fireEvent.click(screen.getByRole("button", { name: "合并所选" }));
  await waitFor(() => expect(api.merge).toHaveBeenCalled());
  fireEvent.click(screen.getAllByRole("button", { name: "上移" })[1]);
  await waitFor(() => expect(api.reorder).toHaveBeenCalled());
  fireEvent.click(screen.getAllByRole("button", { name: "重试区域" })[0]);
  await waitFor(() => expect(api.retry).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: "确认识别结果" }));
  await waitFor(() => expect(api.confirm).toHaveBeenCalled());
});

it("renders finalized evidence as read-only", async () => {
  render(
    <AnswerRecognitionWorkspace
      submissionId="submission-1"
      answerId="answer-1"
      regionIds={["region-1"]}
      readOnly
    />,
  );
  await screen.findByText("finalized · 只读");
  expect(screen.getAllByRole("button", { name: "编辑" })[0]).toBeDisabled();
  expect(screen.getByRole("button", { name: "确认识别结果" })).toBeDisabled();
});
