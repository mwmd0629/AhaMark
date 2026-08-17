"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuthUser } from "@/components/auth-gate";
import { HealthStatus } from "@/components/health-status";
import { Icon } from "@/components/icons";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionHeader,
  Skeleton,
  StatCard,
  Table,
} from "@/components/ui";
import {
  ApiError,
  assignmentsApi,
  classesApi,
  type AssignmentRecord,
  type ClassRecord,
} from "@/lib/api";
import { useSmartRefresh } from "@/lib/use-smart-refresh";

export default function DashboardPage() {
  const user = useAuthUser();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [assignments, setAssignments] = useState<AssignmentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async (background = false) => {
    if (!background) {
      setLoading(true);
      setError("");
    }
    try {
      const [classPage, assignmentPage] = await Promise.all([
        classesApi.list("page_size=100&status=active"),
        assignmentsApi.list("page_size=100"),
      ]);
      setClasses(classPage.items);
      setAssignments(assignmentPage.items);
    } catch (reason) {
      if (!background) {
        setError(
          reason instanceof ApiError ? reason.message : "工作台数据加载失败",
        );
      }
    } finally {
      if (!background) setLoading(false);
    }
  };

  useEffect(() => void load(), []);
  useSmartRefresh(() => load(true), { intervalMs: 60_000 });

  const activeStudents = classes.reduce(
    (total, item) => total + item.active_student_count,
    0,
  );
  const pendingAssignments = assignments.filter(
    (item) => item.status === "draft" || !item.completeness.ready,
  ).length;
  const publishedAssignments = assignments.filter(
    (item) => item.status === "published",
  ).length;

  return (
    <div className="space-y-8">
      <PageHeader
        title={`你好，${user?.display_name || user?.email || "教师"}`}
        description="这里仅展示当前登录教师拥有的真实班级与作业数据。"
        actions={
          <>
            <Link href="/classes">
              <Button variant="outline">创建班级</Button>
            </Link>
            <Link href="/assignments/new">
              <Button>
                <Icon name="plus" className="h-4 w-4" />
                创建作业
              </Button>
            </Link>
          </>
        }
      />
      {loading ? (
        <section
          aria-label="正在加载工作台"
          className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
        >
          {[1, 2, 3, 4].map((item) => (
            <Card key={item} className="p-5">
              <Skeleton className="h-5 w-24" />
              <Skeleton className="mt-4 h-10 w-16" />
            </Card>
          ))}
        </section>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : (
        <>
          <section
            aria-label="当前账号数据概览"
            className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
          >
            <StatCard
              label="活跃班级"
              value={String(classes.length)}
              note="当前教师名下"
            />
            <StatCard
              label="学生"
              value={String(activeStudents)}
              note="活跃班级中的学生"
            />
            <StatCard
              label="作业"
              value={String(assignments.length)}
              note={`${publishedAssignments} 份已发布`}
            />
            <StatCard
              label="待完善作业"
              value={String(pendingAssignments)}
              note="草稿或发布检查未通过"
            />
          </section>
          {classes.length === 0 && assignments.length === 0 ? (
            <EmptyState
              icon="dashboard"
              title="这是一个空白教师账号"
              description="当前账号没有班级、学生或作业。先创建班级，再导入纯合成学生数据并创建第一份作业。"
              action={
                <Link href="/classes">
                  <Button>创建第一个班级</Button>
                </Link>
              }
            />
          ) : (
            <section className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(300px,.5fr)]">
              <Card className="overflow-hidden">
                <div className="p-5">
                  <SectionHeader
                    title="最近作业"
                    description="当前教师最近更新的真实作业"
                    action={
                      <Link
                        href="/assignments"
                        className="text-sm font-semibold text-[var(--brand-700)]"
                      >
                        查看全部
                      </Link>
                    }
                  />
                </div>
                {assignments.length ? (
                  <Table>
                    <thead>
                      <tr className="border-y border-[var(--border)] bg-slate-50 text-xs text-[var(--text-secondary)]">
                        <th className="px-5 py-3">作业</th>
                        <th className="px-4 py-3">班级</th>
                        <th className="px-4 py-3">状态</th>
                        <th className="px-5 py-3">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {assignments.slice(0, 5).map((item) => (
                        <tr
                          key={item.id}
                          className="border-b border-[var(--border)] last:border-0"
                        >
                          <td className="px-5 py-4 font-semibold">
                            {item.title}
                          </td>
                          <td className="px-4 py-4 text-sm">
                            {item.classes.map((x) => x.name).join("、") ||
                              "未选择"}
                          </td>
                          <td className="px-4 py-4">
                            <Badge status={item.status} />
                          </td>
                          <td className="px-5 py-4">
                            <Link
                              href={`/assignments/${item.id}${item.status === "draft" ? "/edit" : ""}`}
                              className="text-sm font-semibold text-[var(--brand-700)]"
                            >
                              查看
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                ) : (
                  <div className="p-6 text-sm text-[var(--text-secondary)]">
                    当前账号还没有作业。
                  </div>
                )}
              </Card>
              <Card className="h-fit p-5">
                <SectionHeader title="班级概览" />
                <div className="mt-4 divide-y divide-[var(--border)]">
                  {classes.slice(0, 5).map((item) => (
                    <Link
                      key={item.id}
                      href={`/classes/${item.id}`}
                      className="flex items-center justify-between gap-3 py-3 text-sm hover:text-[var(--brand-700)]"
                    >
                      <strong>{item.name}</strong>
                      <span className="text-[var(--text-secondary)]">
                        {item.active_student_count} 名学生
                      </span>
                    </Link>
                  ))}
                </div>
              </Card>
            </section>
          )}
        </>
      )}
      <Card className="p-5">
        <SectionHeader title="系统状态" description="当前 API 的真实连接状态" />
        <div className="mt-4">
          <HealthStatus />
        </div>
      </Card>
    </div>
  );
}
