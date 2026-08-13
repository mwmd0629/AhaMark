export const studentNavigation = [
  { href: "/student", label: "学习首页", icon: "dashboard" },
  { href: "/student/assignments", label: "我的作业", icon: "assignments" },
  { href: "/student/results", label: "成绩", icon: "results" },
  { href: "/student/wrong-questions", label: "错题本", icon: "practice" },
  {
    href: "/student/learning-analysis",
    label: "AI 学习分析",
    icon: "analytics",
  },
  { href: "/student/resources", label: "学习资源", icon: "resources" },
] as const;

export function studentPageTitle(pathname: string) {
  const match = [...studentNavigation]
    .sort((left, right) => right.href.length - left.href.length)
    .find(
      (item) =>
        pathname === item.href ||
        (item.href !== "/student" && pathname.startsWith(`${item.href}/`)),
    );
  return match?.label ?? "学生端";
}
