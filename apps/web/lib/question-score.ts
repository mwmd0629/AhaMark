export function formatQuestionScore(score?: string | null): string {
  return score == null || score === "" ? "分值未设置" : `${score} 分`;
}
