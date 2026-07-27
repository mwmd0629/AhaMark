export type ReviewCopy = {
  title: string;
  message: string;
  action: string;
};

const copies: Record<string, ReviewCopy> = {
  SOURCE_STALE: {
    title: "内容已经发生变化",
    message: "题目、试卷或评分标准在审查后被修改，请重新确认最新内容。",
    action: "重新开始审查",
  },
  REVIEW_SOURCE_STALE: {
    title: "内容已经发生变化",
    message: "题目、试卷或评分标准在审查后被修改，请重新确认最新内容。",
    action: "重新开始审查",
  },
  PROVIDER_UNAVAILABLE: {
    title: "自动生成服务暂时不可用",
    message: "系统暂时不能自动生成内容，你仍然可以人工填写并确认。",
    action: "人工检查并继续",
  },
  GENERATION_PARTIAL: {
    title: "部分内容没有自动生成",
    message: "请检查题目、参考答案和评分标准是否完整。",
    action: "检查内容",
  },
  QUESTION_CONFIRMATION_REQUIRED: {
    title: "题目还需要教师确认",
    message: "请确认题目内容、题号和分值无误。",
    action: "确认题目",
  },
  PAPER_VARIANT_REVIEW: {
    title: "请确认试卷页面属于同一份试卷",
    message: "系统无法确定页面是否来自不同版本，请人工查看页面。",
    action: "检查试卷页面",
  },
  NO_CLASSES: {
    title: "还没有选择发布班级",
    message: "至少选择一个班级后才能发布。",
    action: "选择班级",
  },
  DUE_AT_REQUIRED: {
    title: "截止时间未设置",
    message: "当前作业选择了设置截止时间，请选择具体日期和时间；也可以改为无截止时间。",
    action: "返回修改",
  },
};

export function getReviewCopy(code: string): ReviewCopy {
  return (
    copies[code] ?? {
      title: "需要教师检查的内容",
      message: "系统发现一项需要确认的问题，请查看说明并完成处理。",
      action: "查看并处理",
    }
  );
}
