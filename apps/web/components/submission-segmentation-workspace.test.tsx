import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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
  segmentation_version: "submission-seg-v2",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

beforeEach(() => {
  cleanup();
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
    config_version: "submission-processing-v2",
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
    config_version: "submission-processing-v2",
    attempt: 1,
  });
});

it("draws, confirms, deletes, and retries answer regions", async () => {
  render(<SubmissionSegmentationWorkspace submissionId="submission-1" />);
  await screen.findAllByText("图像清晰度较低");

  expect(screen.getByTestId("submission-processing-start")).toHaveTextContent(
    "重新自动切题",
  );
  expect(screen.getByTestId("submission-region-canvas")).toHaveAttribute(
    "data-page-id",
    "page-1",
  );
  expect(screen.getByTestId("submission-question-select")).toHaveValue(
    "question-1",
  );
  expect(screen.getByTestId("submission-region-card")).toHaveAttribute(
    "data-region-id",
    "region-1",
  );

  fireEvent.click(screen.getByTestId("submission-region-confirm"));
  await waitFor(() => expect(mocks.updateRegion).toHaveBeenCalled());

  fireEvent.click(screen.getByTestId("submission-region-delete"));
  await waitFor(() =>
    expect(mocks.removeRegion).toHaveBeenCalledWith("submission-1", "region-1"),
  );

  fireEvent.click(screen.getByTestId("submission-processing-start"));
  await waitFor(() => expect(mocks.start).toHaveBeenCalled());
  expect(
    await screen.findByTestId("submission-processing-job"),
  ).toHaveAttribute("data-status", "failed");
  fireEvent.click(await screen.findByRole("button", { name: "重新处理" }));
  await waitFor(() => expect(mocks.retryPage).toHaveBeenCalled());

  const canvas = screen.getByTestId("submission-region-canvas");
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
  expect(canvas).toHaveAttribute("data-draw-enabled", "false");
  fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 10, clientY: 40 });
  fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 70, clientY: 80 });
  fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 70, clientY: 80 });
  expect(mocks.addRegion).not.toHaveBeenCalled();

  fireEvent.click(screen.getByTestId("submission-region-draw-toggle"));
  expect(canvas).toHaveAttribute("data-draw-enabled", "true");
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

it("keeps the completed default view focused and expands editing on demand", async () => {
  mocks.pages.mockResolvedValue([
    {
      ...page,
      processing_status: "completed",
      quality: { warnings: [] },
      retryable: false,
    },
  ]);
  mocks.regions.mockResolvedValue([{ ...region, status: "confirmed" }]);
  mocks.incomplete.mockResolvedValue({ complete: true, question_ids: [] });

  render(<SubmissionSegmentationWorkspace submissionId="submission-1" />);

  expect(await screen.findByText("切题已完成")).toBeInTheDocument();
  expect(
    screen.getByText("1 道题已匹配，可继续识别答案。"),
  ).toBeInTheDocument();
  expect(
    screen.queryByTestId("submission-processing-start"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByTestId("submission-processing-page"),
  ).not.toBeInTheDocument();
  expect(screen.queryByTestId("page-quality")).not.toBeInTheDocument();
  expect(
    screen.queryByTestId("submission-question-select"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByTestId("submission-region-card"),
  ).not.toBeInTheDocument();

  fireEvent.click(screen.getByTestId("submission-adjust-segmentation"));

  expect(await screen.findByTestId("submission-question-select")).toHaveValue(
    "question-1",
  );
  expect(screen.getByTestId("submission-region-card")).toHaveAttribute(
    "data-region-id",
    "region-1",
  );
  expect(screen.getByTestId("page-quality")).toHaveTextContent("页面调整");
  expect(screen.getByTestId("submission-region-delete")).toBeInTheDocument();
  expect(screen.getByTestId("submission-region-draw-toggle")).toHaveTextContent(
    "开始框选",
  );
  expect(screen.getByTestId("submission-region-canvas")).toHaveAttribute(
    "data-draw-enabled",
    "false",
  );
});

it("keeps an incomplete question without a region selectable for manual drawing", async () => {
  const secondPage = {
    ...page,
    id: "page-2",
    source_page_number: 2,
    page_number: 2,
    processing_status: "completed",
    quality: { warnings: [] },
  };
  mocks.pages.mockResolvedValue([page, secondPage]);
  mocks.incomplete.mockResolvedValue({
    complete: false,
    question_ids: ["question-2"],
  });

  render(<SubmissionSegmentationWorkspace submissionId="submission-1" />);

  const select = await screen.findByTestId("submission-question-select");
  await waitFor(() =>
    expect(select.querySelectorAll("option")).toHaveLength(2),
  );
  expect(
    Array.from(select.querySelectorAll("option")).map((option) => option.value),
  ).toEqual(["question-1", "question-2"]);

  fireEvent.change(select, { target: { value: "question-2" } });
  expect(select).toHaveValue("question-2");

  const secondPageControl = screen
    .getAllByTestId("submission-processing-page")
    .find((item) => item.getAttribute("data-page-id") === "page-2");
  expect(secondPageControl).toBeDefined();
  fireEvent.click(secondPageControl!.querySelector("button")!);

  const canvas = screen.getByTestId("submission-region-canvas");
  await waitFor(() => expect(canvas).toHaveAttribute("data-page-id", "page-2"));
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

  fireEvent.click(screen.getByTestId("submission-region-draw-toggle"));
  fireEvent.pointerDown(canvas, { pointerId: 2, clientX: 3, clientY: 3 });
  fireEvent.pointerMove(canvas, { pointerId: 2, clientX: 97, clientY: 97 });
  fireEvent.pointerUp(canvas, { pointerId: 2, clientX: 97, clientY: 97 });

  await waitFor(() =>
    expect(mocks.addRegion).toHaveBeenCalledWith(
      "submission-1",
      expect.objectContaining({
        question_id: "question-2",
        submission_page_id: "page-2",
        source: "manual",
        status: "confirmed",
        reason: "TEACHER_DRAWN",
      }),
    ),
  );
});

it("keeps only the newest reload when delete and draw refreshes resolve out of order", async () => {
  const { unmount } = render(
    <SubmissionSegmentationWorkspace submissionId="submission-1" />,
  );
  await screen.findByTestId("submission-region-card");

  const canvas = screen.getByTestId("submission-region-canvas");
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
  fireEvent.click(screen.getByTestId("submission-region-draw-toggle"));

  const oldPages = deferred<(typeof page)[]>();
  const oldRegions = deferred<(typeof region)[]>();
  const oldIncomplete = deferred<{
    complete: boolean;
    question_ids: string[];
  }>();
  const newestPage = {
    ...page,
    id: "page-new",
    page_number: 2,
    source_page_number: 2,
    processing_status: "completed",
    quality: { warnings: [] },
  };
  const newestRegion = {
    ...region,
    id: "region-new",
    question_id: "question-new",
    submission_page_id: "page-new",
    status: "confirmed",
  };
  const newPages = deferred<(typeof newestPage)[]>();
  const newRegions = deferred<(typeof newestRegion)[]>();
  const newIncomplete = deferred<{
    complete: boolean;
    question_ids: string[];
  }>();
  mocks.pages
    .mockReturnValueOnce(oldPages.promise)
    .mockReturnValueOnce(newPages.promise);
  mocks.regions
    .mockReturnValueOnce(oldRegions.promise)
    .mockReturnValueOnce(newRegions.promise);
  mocks.incomplete
    .mockReturnValueOnce(oldIncomplete.promise)
    .mockReturnValueOnce(newIncomplete.promise);

  fireEvent.click(screen.getByTestId("submission-region-delete"));
  fireEvent.pointerDown(canvas, { pointerId: 3, clientX: 10, clientY: 10 });
  fireEvent.pointerMove(canvas, { pointerId: 3, clientX: 80, clientY: 80 });
  fireEvent.pointerUp(canvas, { pointerId: 3, clientX: 80, clientY: 80 });

  await waitFor(() => expect(mocks.pages).toHaveBeenCalledTimes(3));
  newPages.resolve([newestPage]);
  newRegions.resolve([newestRegion]);
  newIncomplete.resolve({
    complete: false,
    question_ids: ["question-new"],
  });

  await waitFor(() =>
    expect(screen.getByTestId("submission-question-select")).toHaveValue(
      "question-new",
    ),
  );
  expect(screen.getByTestId("submission-region-canvas")).toHaveAttribute(
    "data-page-id",
    "page-new",
  );
  expect(screen.getByTestId("submission-region-card")).toHaveAttribute(
    "data-region-id",
    "region-new",
  );

  oldPages.resolve([page]);
  oldRegions.resolve([region]);
  oldIncomplete.resolve({
    complete: false,
    question_ids: ["question-1"],
  });
  await oldPages.promise;
  await Promise.resolve();

  expect(screen.getByTestId("submission-question-select")).toHaveValue(
    "question-new",
  );
  expect(screen.getByTestId("submission-region-canvas")).toHaveAttribute(
    "data-page-id",
    "page-new",
  );
  expect(screen.getByTestId("submission-region-card")).toHaveAttribute(
    "data-region-id",
    "region-new",
  );

  unmount();
});
