export type DemoTeacher = { name: string; title: string; initials: string };
export type DemoClass = {
  id: string;
  name: string;
  students: number;
  latestAssignment: string;
  pending: number;
};
export type DemoAssignment = {
  id: string;
  title: string;
  className: string;
  status: "draft" | "processing" | "pending-review" | "completed";
  submissions: string;
  aiProgress: number;
  reviewCount: number;
  due: string;
  average?: number;
  completedAt?: string;
};
export type DemoGradingTask = {
  id: string;
  title: string;
  className: string;
  progress: number;
  reviewCount: number;
  status: DemoAssignment["status"];
};
export type DemoDashboardStats = {
  label: string;
  value: string;
  note: string;
}[];

export const demoTeacher: DemoTeacher = {
  name: "林老师",
  title: "数学教师 · 演示账号",
  initials: "林",
};
export const demoDashboardStats: DemoDashboardStats = [
  { label: "待批改作业", value: "4", note: "2 项今天截止" },
  { label: "待复核题目", value: "28", note: "AI 初批后需教师确认" },
  { label: "本周已批改", value: "186", note: "份学生作业" },
  { label: "当前班级", value: "3", note: "共 126 名学生" },
];
export const demoClasses: DemoClass[] = [
  {
    id: "class-8-3",
    name: "初二（3）班",
    students: 46,
    latestAssignment: "二次函数单元测验",
    pending: 12,
  },
  {
    id: "class-7-2",
    name: "七年级（2）班",
    students: 42,
    latestAssignment: "一元一次不等式练习",
    pending: 8,
  },
  {
    id: "class-8-1",
    name: "初二（1）班",
    students: 38,
    latestAssignment: "三角形全等证明",
    pending: 0,
  },
];
export const demoAssignments: DemoAssignment[] = [
  {
    id: "a1",
    title: "二次函数单元测验",
    className: "初二（3）班",
    status: "pending-review",
    submissions: "43/46",
    aiProgress: 100,
    reviewCount: 12,
    due: "今天 18:00",
  },
  {
    id: "a2",
    title: "一元一次不等式练习",
    className: "七年级（2）班",
    status: "processing",
    submissions: "39/42",
    aiProgress: 68,
    reviewCount: 8,
    due: "明天 17:00",
  },
  {
    id: "a3",
    title: "三角形全等证明",
    className: "初二（1）班",
    status: "pending-review",
    submissions: "38/38",
    aiProgress: 100,
    reviewCount: 5,
    due: "7 月 24 日",
  },
  {
    id: "a4",
    title: "整式乘法随堂练习",
    className: "初二（3）班",
    status: "draft",
    submissions: "0/46",
    aiProgress: 0,
    reviewCount: 0,
    due: "未发布",
  },
  {
    id: "a5",
    title: "相交线与平行线",
    className: "七年级（2）班",
    status: "completed",
    submissions: "42/42",
    aiProgress: 100,
    reviewCount: 0,
    due: "已结束",
    average: 84.6,
    completedAt: "7 月 19 日",
  },
];
export const demoGradingTasks: DemoGradingTask[] = demoAssignments
  .slice(0, 3)
  .map((item) => ({
    id: item.id,
    title: item.title,
    className: item.className,
    progress: item.aiProgress,
    reviewCount: item.reviewCount,
    status: item.status,
  }));
export const demoRubrics = [
  {
    name: "数学主观题通用评分模板",
    subject: "数学",
    status: "已启用",
    updatedAt: "2026-07-20",
  },
  {
    name: "几何证明题分步评分",
    subject: "数学",
    status: "草稿",
    updatedAt: "2026-07-18",
  },
];
