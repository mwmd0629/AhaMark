import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { SubmissionSegmentationWorkspace } from "./submission-segmentation-workspace";

const mocks = vi.hoisted(() => ({
  pages: vi.fn(),
  regions: vi.fn(),
  incomplete: vi.fn(),
  addRegion: vi.fn(),
  updateRegion: vi.fn(),
  removeRegion: vi.fn(),
  confirmHighConfidence: vi.fn(),
  retryPage: vi.fn(),
  start: vi.fn(),
  job: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  submissionProcessingApi: mocks,
}));

const page = {
  id: "page-1",
  source_page_number: 1,
  page_number: 1,
  rotation: 0,
  processing_status: "failed",
  quality: { warnings: ["LOW_SHARPNESS"] },
  retryable: true,
  original_url: "/original.png",
  processed_url: "/processed.png",
  thumbnail_url: "/thumb.png",
};

const region = {
  id: "region-1",
  question_id: "question-1",
  student_answer_id: "answer-1",
  submission_page_id: "page-1",
  x: 0.1,
  y: 0.1,
  width: 0.5,
  height: 0.2,
  source: "ocr",
  confidence: 0.91,
  status: "candidate",
  reason: "QUESTION_ANCHOR",
  segmentation_version: "submission-seg-v1",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.pages.mockResolvedValue([page]);
  mocks.regions.mockResolvedValue([region]);
  mocks.incomplete.mockResolvedValue({
    complete: false,
    question_ids: ["question-1"],
  });
  mocks.updateRegion.mockResolvedValue({ ...region, status: "confirmed" });
  mocks.removeRegion.mockResolvedValue(undefined);
  mocks.addRegion.mockResolvedValue({ ...region, id: "region-2" });
  mocks.confirmHighConfidence.mockResolvedValue({ confirmed_count: 1 });
  mocks.retryPage.mockResolvedValue({
    id: "job-1",
    submission_id: "submission-1",
    status: "queued",
    stage: "page_processing",
    progress: 0,
    provider: "local",
    provider_version: "pillow",
    config_version: "submission-processing-v1",
    attempt: 1,
  });
  mocks.start.mockResolvedValue({
    id: "job-1",
    submission_id: "submission-1",
    status: "failed",
    stage: "completed",
    progress: 100,
    provider: "local",
    provider_version: "pillow",
    config_version: "submission-processing-v1",
    attempt: 1,
  });
});

it("draws, confirms, deletes, and retries answer regions", async () => {
  render(<SubmissionSegmentationWorkspace submissionId="submission-1" />);
  await screen.findAllByText("图像清晰度较低");

  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  await waitFor(() => expect(mocks.updateRegion).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  await waitFor(() =>
    expect(mocks.removeRegion).toHaveBeenCalledWith("submission-1", "region-1"),
  );

  fireEvent.click(screen.getByRole("button", { name: "处理并自动切题" }));
  await waitFor(() => expect(mocks.start).toHaveBeenCalled());
  fireEvent.click(await screen.findByRole("button", { name: "重新处理" }));
  await waitFor(() => expect(mocks.retryPage).toHaveBeenCalled());

  const canvas = screen.getByLabelText("框选题目区域");
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
  canvas.setPointerCapture = vi.fn();
  canvas.releasePointerCapture = vi.fn();
  fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 10, clientY: 40 });
  fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 70, clientY: 80 });
  fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 70, clientY: 80 });
  await waitFor(() =>
    expect(mocks.addRegion).toHaveBeenCalledWith(
      "submission-1",
      expect.objectContaining({
        question_id: "question-1",
        submission_page_id: "page-1",
        source: "manual",
        status: "confirmed",
      }),
    ),
  );
});
