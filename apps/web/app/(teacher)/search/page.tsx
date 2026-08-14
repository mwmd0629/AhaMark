"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuthUser } from "@/components/auth-gate";
import { Icon } from "@/components/icons";
import {
  Button,
  Card,
  ErrorState,
  PageHeader,
  Skeleton,
} from "@/components/ui";
import {
  ApiError,
  assignmentsApi,
  classesApi,
  type AssignmentRecord,
  type ClassRecord,
} from "@/lib/api";
import { useSmartRefresh } from "@/lib/use-smart-refresh";

type SearchCategory = "全部" | "功能" | "班级" | "作业";
type SearchEntry = {
  id: string;
  title: string;
  description: string;
  keywords: string;
  href: string;
  category: Exclude<SearchCategory, "全部">;
  updatedAt?: string;
};

const featureEntries: SearchEntry[] = [
  {
    id: "new-assignment",
    title: "创建作业",
    description: "上传试卷、参考答案和评分标准。",
    keywords: "新建 上传 pdf 多文件 题目",
    href: "/assignments/new",
    category: "功能",
  },
  {
    id: "grading",
    title: "批改与复核",
    description: "上传答卷、检查切题、确认 AI 建议分。",
    keywords: "识别 框选 切题 评分 确认建议分",
    href: "/grading",
    category: "功能",
  },
  {
    id: "classes",
    title: "班级与学生",
    description: "管理班级、学生和名单导入。",
    keywords: "创建班级 学生 名单 csv xlsx",
    href: "/classes",
    category: "功能",
  },
  {
    id: "analytics",
    title: "学情分析",
    description: "查看成绩分布、题目和学生表现。",
    keywords: "统计 报告 成绩 知识点",
    href: "/analytics",
    category: "功能",
  },
  {
    id: "notifications",
    title: "待办提醒",
    description: "查看当前账号需要继续处理的事项。",
    keywords: "消息 未读 任务 提醒",
    href: "/notifications",
    category: "功能",
  },
  {
    id: "help",
    title: "使用帮助",
    description: "查找操作步骤和常见问题。",
    keywords: "教程 怎么办 说明",
    href: "/help",
    category: "功能",
  },
];

const statusLabel: Record<AssignmentRecord["status"], string> = {
  draft: "草稿",
  published: "已发布",
  grading: "批改中",
  completed: "已完成",
  archived: "已归档",
};

function buildSearchEntries(
  classes: ClassRecord[],
  assignments: AssignmentRecord[],
): SearchEntry[] {
  return [
    ...featureEntries,
    ...classes.map((item) => ({
      id: item.id,
      title: item.name,
      description: `${[item.grade, item.subject].filter(Boolean).join(" · ") || "未填写年级与学科"} · ${item.active_student_count} 名学生`,
      keywords: `${item.description || ""} ${item.academic_year || ""} ${item.semester || ""} ${item.status === "active" ? "使用中" : "已归档"}`,
      href: `/classes/${item.id}`,
      category: "班级" as const,
      updatedAt: item.updated_at,
    })),
    ...assignments.map((item) => ({
      id: item.id,
      title: item.title,
      description: `${item.classes.map((entry) => entry.name).join("、") || "未选择班级"} · ${statusLabel[item.status]}`,
      keywords: `${item.description || ""} ${item.subject || ""} ${item.grade || ""} ${statusLabel[item.status]} ${item.classes.map((entry) => entry.name).join(" ")}`,
      href: `/assignments/${item.id}${item.status === "draft" ? "/edit" : ""}`,
      category: "作业" as const,
      updatedAt: item.updated_at,
    })),
  ];
}

function filterSearchEntries(
  entries: SearchEntry[],
  query: string,
  category: SearchCategory,
) {
  const tokens = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return entries
    .filter((item) => category === "全部" || item.category === category)
    .filter((item) => {
      if (!tokens.length)
        return category === "全部" ? item.category === "功能" : true;
      const haystack =
        `${item.title} ${item.description} ${item.keywords} ${item.category}`.toLocaleLowerCase();
      return tokens.every((token) => haystack.includes(token));
    })
    .sort((left, right) => {
      if (left.category === "功能" && right.category !== "功能") return -1;
      if (right.category === "功能" && left.category !== "功能") return 1;
      return (right.updatedAt || "").localeCompare(left.updatedAt || "");
    });
}

export default function SearchPage() {
  const user = useAuthUser();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<SearchCategory>("全部");
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [assignments, setAssignments] = useState<AssignmentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (background = false) => {
    if (!background) {
      setLoading(true);
      setError("");
    }
    try {
      const [classPage, assignmentPage] = await Promise.all([
        classesApi.list("page_size=100"),
        assignmentsApi.list("page_size=100"),
      ]);
      setClasses(classPage.items);
      setAssignments(assignmentPage.items);
    } catch (reason) {
      if (!background) {
        setError(
          reason instanceof ApiError ? reason.message : "当前账号内容加载失败",
        );
      }
    } finally {
      if (!background) setLoading(false);
    }
  }, []);

  useEffect(() => void load(), [load]);
  useSmartRefresh(() => load(true), { intervalMs: 60_000 });

  const entries = useMemo(
    () => buildSearchEntries(classes, assignments),
    [assignments, classes],
  );
  const results = useMemo(
    () => filterSearchEntries(entries, query, category),
    [category, entries, query],
  );
  const normalized = query.trim();
  const categoryCounts = useMemo(
    () =>
      Object.fromEntries(
        (["全部", "功能", "班级", "作业"] as const).map((item) => [
          item,
          filterSearchEntries(entries, query, item).length,
        ]),
      ) as Record<SearchCategory, number>,
    [entries, query],
  );
  const accountName = user?.display_name || user?.email || "当前账号";

  return (
    <div className="space-y-6">
      <PageHeader
        title="搜索"
        description={`查找 ${accountName} 名下的作业、班级和常用功能。`}
      />
      <Card className="p-5">
        <div className="flex gap-2">
          <label className="relative block flex-1">
            <span className="sr-only">输入搜索关键词</span>
            <Icon
              name="search"
              className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400"
            />
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="输入作业名、班级名或想做的操作"
              className="h-12 w-full rounded-xl border border-[var(--border)] bg-white pl-12 pr-4 outline-none transition focus:border-[var(--brand-500)]"
            />
          </label>
          {normalized && (
            <Button variant="outline" onClick={() => setQuery("")}>
              清空
            </Button>
          )}
        </div>
        <div className="mt-4 flex flex-wrap gap-2" aria-label="搜索范围">
          {(["全部", "功能", "班级", "作业"] as const).map((item) => (
            <button
              key={item}
              type="button"
              aria-pressed={category === item}
              onClick={() => setCategory(item)}
              className={`rounded-full px-3 py-1.5 text-sm font-medium transition ${
                category === item
                  ? "bg-[var(--brand-600)] text-white"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200"
              }`}
            >
              {item} {categoryCounts[item]}
            </button>
          ))}
        </div>
      </Card>

      {loading ? (
        <section
          aria-label="正在加载搜索内容"
          className="grid gap-3 md:grid-cols-2"
        >
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
        </section>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : (
        <section aria-label="搜索结果" aria-live="polite">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">
              {normalized ? `“${normalized}”的结果` : "常用入口"}
            </h2>
            <span className="text-sm text-[var(--text-secondary)]">
              {results.length} 项
            </span>
          </div>
          {results.length ? (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {results.map((item) => (
                <Link
                  key={`${item.category}-${item.id}`}
                  href={item.href}
                  className="group rounded-xl border border-[var(--border)] bg-white p-5 shadow-[var(--shadow-sm)] transition hover:-translate-y-0.5 hover:border-[var(--brand-500)]"
                >
                  <span className="flex items-center justify-between gap-3">
                    <span className="text-xs font-semibold text-[var(--brand-700)]">
                      {item.category}
                    </span>
                    <Icon
                      name="chevron"
                      className="h-4 w-4 text-slate-300 transition group-hover:text-[var(--brand-600)]"
                    />
                  </span>
                  <h3 className="mt-2 font-semibold">{item.title}</h3>
                  <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                    {item.description}
                  </p>
                </Link>
              ))}
            </div>
          ) : (
            <Card className="p-10 text-center">
              <Icon name="search" className="mx-auto h-8 w-8 text-slate-300" />
              <h3 className="mt-4 font-semibold">没有找到匹配内容</h3>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">
                试试更短的关键词，或切换到“全部”。
              </p>
            </Card>
          )}
        </section>
      )}
    </div>
  );
}
