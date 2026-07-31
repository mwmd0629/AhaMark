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
    message:
      "当前作业选择了设置截止时间，请选择具体日期和时间；也可以改为无截止时间。",
    action: "返回修改",
  },
  CONFIRM_CLASSES_REQUIRED: {
    title: "发布班级尚未确认",
    message: "班级已经选择，但还需要在本页点击“确认班级”。",
    action: "检查班级",
  },
  CONFIRM_DUE_AT_REQUIRED: {
    title: "截止时间尚未确认",
    message: "请核对截止时间，然后在本页点击“确认截止时间”。",
    action: "检查截止时间",
  },
  CONFIRM_TOTAL_SCORE_REQUIRED: {
    title: "作业总分尚未确认",
    message: "请先确保作业总分等于所有题目分值之和，再点击“确认总分”。",
    action: "检查总分",
  },
  TOTAL_SCORE_MISMATCH: {
    title: "作业总分与题目合计不一致",
    message: "请修改作业总分或题目分值，使二者完全一致。",
    action: "修改分值",
  },
  CONFIRM_FILE_ROLES_REQUIRED: {
    title: "试卷文件角色尚未确认",
    message: "请确认哪些文件是试卷、参考答案或其他附件。",
    action: "检查文件",
  },
  FILE_ROLE_UNCONFIRMED: {
    title: "有文件尚未指定用途",
    message: "至少一个上传文件还没有确认是试卷、参考答案还是附件。",
    action: "确认文件用途",
  },
  CONFIRM_ANSWER_SOURCES_REQUIRED: {
    title: "参考答案来源尚未确认",
    message: "请核对参考答案来自教师、出版社、第三方还是 AI，并完成确认。",
    action: "检查答案来源",
  },
  ANSWER_SOURCE_UNCONFIRMED: {
    title: "有参考答案的来源未确认",
    message: "至少一个参考答案文件仍处于来源未知状态。",
    action: "确认答案来源",
  },
  ANSWER_SOURCE_CONFIRMATION_REQUIRED: {
    title: "答案文件需要确认来源",
    message: "请返回文件分析，确认答案文件的用途及来源。",
    action: "检查答案文件",
  },
  ANSWER_SOURCE_REVIEW: {
    title: "AI 生成的参考答案需要复核",
    message: "该题答案由 AI 生成，不会被标记为教师或出版社官方答案。",
    action: "检查参考答案",
  },
  CONFIRM_PAPER_VERSION_REQUIRED: {
    title: "当前试卷版本尚未确认",
    message: "请核对页面顺序和旋转方向，再确认当前试卷版本。",
    action: "检查试卷页面",
  },
  CONFIRM_REFERENCE_ANSWERS_REQUIRED: {
    title: "参考答案版本尚未确认",
    message: "参考答案已经生成，但还需要确认本次发布使用这些版本。",
    action: "检查参考答案",
  },
  CONFIRM_STRUCTURED_RUBRICS_REQUIRED: {
    title: "评分标准版本尚未确认",
    message: "结构化评分标准已经生成，但还需要确认本次发布使用这些版本。",
    action: "检查评分标准",
  },
  LEGACY_CONVERSION_REVIEW: {
    title: "评分标准兼容版需要人工核查",
    message: "完整评分标准保持不变；具体需要人工核查的影响已列在兼容说明中。",
    action: "查看兼容说明",
  },
  CONFIRM_LEGACY_BINDING_REQUIRED: {
    title: "评分标准兼容方式等待确认",
    message: "请阅读兼容说明，并确认发布后需要人工核查的具体内容。",
    action: "查看兼容说明",
  },
  LEGACY_BINDING_REQUIRED: {
    title: "尚未生成评分标准兼容版本",
    message: "请为当前答案和评分标准生成本次发布使用的兼容版本。",
    action: "生成兼容版本",
  },
  LEGACY_BINDING_STALE: {
    title: "评分标准兼容版本需要重新生成",
    message: "答案或评分标准已经变化，旧兼容版本不再适用于当前内容。",
    action: "重新生成兼容版本",
  },
  FILE_ROLE_REVIEW_REQUIRED: {
    title: "文件用途需要人工复核",
    message: "自动分析无法替你确认文件用途，请核对试卷和答案文件。",
    action: "检查文件",
  },
  MANUAL_REVIEW_REQUIRED: {
    title: "生成内容需要人工复核",
    message: "请检查生成的题目、参考答案和评分标准后再继续发布。",
    action: "检查生成内容",
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
