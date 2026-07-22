import { describe, expect, it } from "vitest";

import { formatQuestionScore } from "./question-score";

describe("formatQuestionScore", () => {
  it("does not invent a score for an unknown value", () => {
    expect(formatQuestionScore(null)).toBe("分值未设置");
    expect(formatQuestionScore(undefined)).toBe("分值未设置");
    expect(formatQuestionScore("")).toBe("分值未设置");
  });

  it("renders an explicitly configured score", () => {
    expect(formatQuestionScore("5")).toBe("5 分");
  });
});
