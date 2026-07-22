import Link from "next/link";
import { HealthStatus } from "@/components/health-status";
import { Icon } from "@/components/icons";
import {
  Badge,
  Button,
  Card,
  DemoBadge,
  PageHeader,
  Progress,
  SectionHeader,
  StatCard,
  Table,
} from "@/components/ui";
import {
  demoAssignments,
  demoClasses,
  demoDashboardStats,
  demoTeacher,
} from "@/lib/demo-data";
export default function DashboardPage() {
  const recent = demoAssignments.filter((item) => item.status === "completed");
  return (
    <div className="space-y-8">
      <PageHeader
        title={`早上好，${demoTeacher.name}`}
        description="今天优先复核低置信度题目，再处理即将截止的作业。所有统计均为界面演示数据。"
        eyebrow={
          <div className="mb-3">
            <DemoBadge />
          </div>
        }
        actions={
          <>
            <Link href="/assignments">
              <Button>
                <Icon name="plus" className="h-4 w-4" />
                创建作业
              </Button>
            </Link>
            <Link href="/grading">
              <Button variant="outline">开始批改</Button>
            </Link>
          </>
        }
      />
      <section
        aria-label="核心数据概览"
        className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
      >
        {demoDashboardStats.map((stat) => (
          <StatCard key={stat.label} {...stat} />
        ))}
      </section>
      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.7fr)_minmax(300px,.7fr)]">
        <Card className="overflow-hidden">
          <div className="p-5">
            <SectionHeader
              title="待处理工作"
              description="按截止时间与待复核数量排序"
              action={<DemoBadge />}
            />
          </div>
          <Table>
            <thead>
              <tr className="border-y border-[var(--border)] bg-slate-50 text-xs text-[var(--text-secondary)]">
                <th className="px-5 py-3 font-semibold">作业 / 班级</th>
                <th className="px-4 py-3 font-semibold">提交</th>
                <th className="px-4 py-3 font-semibold">AI 初批</th>
                <th className="px-4 py-3 font-semibold">状态</th>
                <th className="px-4 py-3 font-semibold">截止</th>
                <th className="px-5 py-3">
                  <span className="sr-only">操作</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {demoAssignments.slice(0, 4).map((item) => (
                <tr
                  key={item.id}
                  className="border-b border-[var(--border)] last:border-0"
                >
                  <td className="px-5 py-4">
                    <strong className="block">{item.title}</strong>
                    <span className="text-xs text-[var(--text-secondary)]">
                      {item.className}
                    </span>
                  </td>
                  <td className="px-4 py-4">{item.submissions}</td>
                  <td className="w-36 px-4 py-4">
                    <Progress value={item.aiProgress} />
                  </td>
                  <td className="px-4 py-4">
                    <Badge status={item.status} />
                    {item.reviewCount > 0 && (
                      <div className="mt-1 text-xs text-amber-700">
                        {item.reviewCount} 题待复核
                      </div>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-4 text-sm">
                    {item.due}
                  </td>
                  <td className="px-5 py-4">
                    <Link
                      className="font-semibold text-[var(--brand-700)] hover:underline"
                      href="/grading"
                    >
                      进入批改
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
        <div className="space-y-6">
          <Card className="p-5">
            <SectionHeader title="快速操作" />
            <div className="mt-4 grid grid-cols-2 gap-3">
              {[
                ["创建作业", "/assignments", "plus"],
                ["上传作业", "/grading", "upload"],
                ["导入学生", "/classes", "classes"],
                ["查看报告", "/analytics", "analytics"],
              ].map(([label, href, icon]) => (
                <Link
                  key={label}
                  href={href}
                  className="rounded-xl border border-[var(--border)] p-4 text-sm font-semibold transition hover:border-[var(--brand-500)] hover:bg-[var(--brand-50)]"
                >
                  <Icon
                    name={icon}
                    className="mb-3 h-5 w-5 text-[var(--brand-700)]"
                  />
                  {label}
                </Link>
              ))}
            </div>
          </Card>
          <Card className="p-5">
            <SectionHeader
              title="系统状态"
              description="仅 API 为真实检测结果"
            />
            <div className="mt-4">
              <HealthStatus />
            </div>
            <p className="mt-4 rounded-lg bg-slate-50 p-3 text-xs leading-5 text-[var(--text-secondary)]">
              Redis、Worker 与 MinIO
              尚未完成真实联调，因此不展示静态“正常”状态。
            </p>
          </Card>
        </div>
      </section>
      <section className="grid gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <SectionHeader
            title="班级概览"
            action={
              <Link
                href="/classes"
                className="text-sm font-semibold text-[var(--brand-700)]"
              >
                查看全部
              </Link>
            }
          />
          <div className="mt-4 divide-y divide-[var(--border)]">
            {demoClasses.map((item) => (
              <div
                key={item.id}
                className="flex items-center justify-between gap-4 py-4"
              >
                <div>
                  <strong>{item.name}</strong>
                  <p className="mt-1 text-xs text-[var(--text-secondary)]">
                    {item.students} 名学生 · 最近：{item.latestAssignment}
                  </p>
                </div>
                <span className="text-sm text-[var(--text-secondary)]">
                  {item.pending ? `${item.pending} 项待处理` : "暂无待处理"}
                </span>
              </div>
            ))}
          </div>
        </Card>
        <Card className="p-5">
          <SectionHeader title="最近完成" action={<DemoBadge />} />
          {recent.length ? (
            <div className="mt-4">
              {recent.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between rounded-xl bg-slate-50 p-4"
                >
                  <div>
                    <strong>{item.title}</strong>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                      {item.className} · {item.completedAt}
                    </p>
                  </div>
                  <div className="text-right">
                    <span className="text-xl font-bold">{item.average}</span>
                    <p className="text-xs text-[var(--text-secondary)]">
                      平均分
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </Card>
      </section>
    </div>
  );
}
