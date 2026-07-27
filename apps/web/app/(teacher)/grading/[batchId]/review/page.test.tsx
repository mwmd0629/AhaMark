import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ReviewPage from "./page";

const mocks = vi.hoisted(() => ({
  reviewWorkspace: vi.fn(),
  finalize: vi.fn(),
  correctAnswer: vi.fn(),
  grade: vi.fn(),
  review: vi.fn(),
}));
const recognitionMocks = vi.hoisted(() => ({
  blocks: vi.fn().mockResolvedValue([]),
  edit: vi.fn(),
  split: vi.fn(),
  merge: vi.fn(),
  reorder: vi.fn(),
  confirm: vi.fn(),
  retry: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ batchId: "b1" }),
}));
vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));
vi.mock("@/lib/api", () => ({
  gradingApi: mocks,
  answerRecognitionApi: recognitionMocks,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function workspace({
  stale = false,
  reviewed = 1,
}: {
  stale?: boolean;
  reviewed?: number;
} = {}) {
  return {
    provider_notice: "合成测试 Provider",
    progress: { reviewed, total: 1 },
    items: [
      {
        submission_id: "sub-1",
        status: stale ? "review_required" : "reviewed",
        pages: [],
        answers: [
          {
            id: "ans-1",
            status: stale ? "stale" : "reviewed",
            requires_review: stale,
            recognized_text: "A",
            corrected_text: undefined,
            question: {
              number: 1,
              type: "single_choice",
              content: "合成题目",
            },
            result: {
              score: "10",
              confidence: "0.99",
              provider: "objective-rule",
              provider_version: "1",
              status: stale ? "stale" : "suggested",
              rubric_version_id: stale ? "rubric-2" : "rubric-1",
            },
            review: stale ? undefined : { final_score: "10", feedback: "" },
            criteria: [],
            evidence: [],
          },
        ],
      },
    ],
  };
}

it("blocks accepting a stale result and offers explicit regrading", async () => {
  mocks.reviewWorkspace.mockResolvedValue(
    workspace({ stale: true, reviewed: 0 }),
  );
  mocks.grade.mockResolvedValue({});
  render(<ReviewPage />);

  expect(await screen.findByTestId("regrade-required")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "接受" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "重新批改" }));
  await waitFor(() => expect(mocks.grade).toHaveBeenCalledWith("ans-1"));
});

it("allows an explicit teacher acceptance for a current low-confidence suggestion", async () => {
  const data = workspace();
  data.items[0].answers[0].requires_review = true;
  data.items[0].answers[0].status = "review_required";
  mocks.reviewWorkspace.mockResolvedValue(data);
  mocks.review.mockResolvedValue({});
  render(<ReviewPage />);

  const accept = await screen.findByRole("button", { name: "接受" });
  expect(accept).toBeEnabled();
  fireEvent.click(accept);
  await waitFor(() =>
    expect(mocks.review).toHaveBeenCalledWith("ans-1", {
      decision: "accepted",
    }),
  );
});

it("surfaces incomplete finalize blockers instead of a false success", async () => {
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  mocks.finalize.mockResolvedValue({
    id: "snapshot-incomplete",
    submission_id: "sub-1",
    status: "incomplete",
    problems: [{ code: "RUBRIC_VERSION_STALE", question_id: "q1" }],
  });
  render(<ReviewPage />);

  fireEvent.click(
    await screen.findByRole("button", { name: "完成全部 finalize" }),
  );
  expect(
    await screen.findByText(/finalize 已阻止 1 份未完成 Submission/),
  ).toHaveTextContent("RUBRIC_VERSION_STALE");
  expect(screen.getByTestId("score-snapshot")).toHaveAttribute(
    "data-status",
    "incomplete",
  );
});
