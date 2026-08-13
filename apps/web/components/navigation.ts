export const navigation = [
  { href: "/dashboard", label: "工作台", icon: "dashboard" },
  { href: "/grading", label: "AI 批改", icon: "grading" },
  { href: "/assignments", label: "作业管理", icon: "assignments" },
  { href: "/classes", label: "班级与学生", icon: "classes" },
  { href: "/analytics", label: "学情分析", icon: "analytics" },
  { href: "/practice", label: "错题与练习", icon: "practice" },
  { href: "/resources", label: "教学资源", icon: "resources" },
  { href: "/review-requests", label: "学生复核", icon: "review" },
  { href: "/rubrics", label: "评分模板", icon: "rubrics" },
  { href: "/settings", label: "设置", icon: "settings" },
] as const;
export const pageTitles = Object.fromEntries(
  navigation.map((item) => [item.href, item.label]),
);
Object.assign(pageTitles, {
  "/search": "搜索",
  "/notifications": "消息中心",
  "/help": "使用帮助",
});
