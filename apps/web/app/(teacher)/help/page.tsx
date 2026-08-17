"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { Card, PageHeader } from "@/components/ui";

const guides = [
  {
    title: "建立班级",
    description: "创建班级，添加或导入学生名单。",
    href: "/classes",
    icon: "classes",
  },
  {
    title: "创建作业",
    description: "上传试卷和参考资料，核对题目与评分标准。",
    href: "/assignments",
    icon: "assignments",
  },
  {
    title: "批改与复核",
    description: "上传答卷，检查切题和识别结果，确认建议分。",
    href: "/grading",
    icon: "grading",
  },
  {
    title: "查看学情",
    description: "成绩确认发布后，查看班级和学生分析。",
    href: "/analytics",
    icon: "analytics",
  },
];

const workflow = [
  ["准备班级", "建立班级并确认学生名单。"],
  ["准备作业", "上传试卷；参考答案和评分标准可以同时上传。"],
  ["核对题目", "检查题目、答案和评分标准，再发布作业。"],
  ["上传答卷", "一次可选择多份答卷，系统会依次处理。"],
  ["检查并批改", "先确认每题框选范围，再查看识别文字和 AI 建议分。"],
  ["教师确认", "逐题确认或修改分数，最后再发布成绩。"],
] as const;

const questions = [
  {
    keywords: "上传 多文件 两个 pdf 参考答案 评分标准",
    question: "可以一次上传多个文件吗？",
    answer:
      "可以。在作业上传页一次选择多个 PDF、PNG 或 JPG，系统会按选择顺序自动上传。单个文件不能超过 25 MB。",
  },
  {
    keywords: "切题 框选 答案 没框准 重新识别",
    question: "答题框没有框准，怎么办？",
    answer:
      "在批改页进入切题处理，删除错误框后重新拖动框选。框内应包含这一题的完整解答，但不要带入下一题内容。保存后再继续识别和批改。",
  },
  {
    keywords: "AI 建议分 确认 分数 复核",
    question: "AI 给出的分数在哪里确认？",
    answer:
      "进入批改批次后点击“检查结果”。每题会显示 AI 建议分和理由；内容无异常时点击“确认建议分”，需要调整时点击“修改分数”。",
  },
  {
    keywords: "自动保存 草稿 离开 恢复",
    question: "修改分数时离开页面会丢失吗？",
    answer:
      "尚未提交的分数和反馈会自动保存在当前浏览器。直接离开时页面也会提醒你。换设备或识别结果更新后，不会恢复旧草稿。",
  },
  {
    keywords: "撤回 取消 确认 错误",
    question: "确认错了可以撤回吗？",
    answer:
      "刚确认的题目可在 5 分钟内撤回并重新处理。系统会保留修改记录，不会悄悄删除原来的确认历史。",
  },
  {
    keywords: "自动 正式成绩 发布",
    question: "AI 建议会自动成为正式成绩吗？",
    answer:
      "不会。AI 只提供建议，必须由教师确认。系统也不会在最后一题后自动发布成绩，发布仍需教师明确操作。",
  },
  {
    keywords: "缺交 零分 统计",
    question: "缺交学生会按零分统计吗？",
    answer:
      "不会。缺交或尚未完成确认的答卷不会被自动记为零分，也不会混入已发布成绩的统计。",
  },
  {
    keywords: "手写 数学 公式 识别",
    question: "手写数学和公式一定能识别吗？",
    answer:
      "不能保证。字迹模糊、复杂公式、表格和图形都可能识别不准。页面出现异常提示时，请以原卷为准核对并修正。",
  },
  {
    keywords: "真实 学生 数据 隐私",
    question: "现在可以使用真实学生数据吗？",
    answer:
      "不可以。当前版本只用于内部演示和开发测试，请使用合成的姓名、学号、试卷、答案和成绩。",
  },
];

export default function HelpPage() {
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLocaleLowerCase();
  const visibleQuestions = useMemo(
    () =>
      normalized
        ? questions.filter((item) =>
            `${item.question} ${item.answer} ${item.keywords}`
              .toLocaleLowerCase()
              .includes(normalized),
          )
        : questions,
    [normalized],
  );

  return (
    <div className="space-y-8">
      <PageHeader
        title="使用帮助"
        description="按教师正在做的事情查找入口和解决办法。"
      />

      <Card className="p-5">
        <label className="relative block">
          <span className="sr-only">搜索使用帮助</span>
          <Icon
            name="search"
            className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400"
          />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="例如：切题、确认分数、一次上传多个文件"
            className="h-12 w-full rounded-xl border border-[var(--border)] bg-white pl-12 pr-4 outline-none transition focus:border-[var(--brand-500)]"
          />
        </label>
      </Card>

      {!normalized && (
        <>
          <section>
            <h2 className="mb-4 text-lg font-semibold">常用入口</h2>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {guides.map((guide) => (
                <Link
                  key={guide.title}
                  href={guide.href}
                  className="rounded-xl border border-[var(--border)] bg-white p-5 shadow-[var(--shadow-sm)] transition hover:-translate-y-0.5 hover:border-[var(--brand-500)]"
                >
                  <span className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--brand-50)] text-[var(--brand-700)]">
                    <Icon name={guide.icon} />
                  </span>
                  <h3 className="mt-4 font-semibold">{guide.title}</h3>
                  <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                    {guide.description}
                  </p>
                </Link>
              ))}
            </div>
          </section>

          <Card className="p-5">
            <h2 className="text-lg font-semibold">完整流程</h2>
            <ol className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {workflow.map(([title, description], index) => (
                <li
                  key={title}
                  className="flex gap-3 rounded-xl bg-slate-50 p-4"
                >
                  <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[var(--brand-600)] text-xs font-bold text-white">
                    {index + 1}
                  </span>
                  <span>
                    <strong className="text-sm">{title}</strong>
                    <span className="mt-1 block text-sm leading-6 text-[var(--text-secondary)]">
                      {description}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          </Card>
        </>
      )}

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(280px,.5fr)]">
        <Card className="p-5">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">
              {normalized ? "搜索结果" : "常见问题"}
            </h2>
            {normalized && (
              <span className="text-sm text-[var(--text-secondary)]">
                {visibleQuestions.length} 条
              </span>
            )}
          </div>
          {visibleQuestions.length ? (
            <div className="mt-4 divide-y divide-[var(--border)]">
              {visibleQuestions.map((item) => (
                <details
                  key={item.question}
                  className="group py-4"
                  open={!!normalized}
                >
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-4 font-medium">
                    {item.question}
                    <Icon
                      name="chevron"
                      className="h-4 w-4 rotate-90 transition group-open:-rotate-90"
                    />
                  </summary>
                  <p className="mt-3 pr-8 text-sm leading-6 text-[var(--text-secondary)]">
                    {item.answer}
                  </p>
                </details>
              ))}
            </div>
          ) : (
            <div className="py-10 text-center">
              <p className="font-medium">没有找到相关帮助</p>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">
                换一个更短的关键词，或查看右侧的问题反馈说明。
              </p>
            </div>
          )}
        </Card>
        <Card className="h-fit p-5">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-amber-100 text-amber-800">
            <Icon name="help" />
          </span>
          <h2 className="mt-4 font-semibold">仍然遇到问题？</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
            请记录你所在的页面、点击了什么、看到了什么提示。截图前请遮住密码和真实学生信息。
          </p>
          <div className="mt-4 flex flex-wrap gap-4 text-sm font-semibold text-[var(--brand-700)]">
            <Link href="/notifications" className="hover:underline">
              查看待办提醒
            </Link>
            <Link href="/settings" className="hover:underline">
              检查系统状态
            </Link>
          </div>
        </Card>
      </section>
    </div>
  );
}
