import Link from "next/link";
import { Icon } from "@/components/icons";
import { Card, DemoBadge, PageHeader } from "@/components/ui";

const guides = [
  {
    title: "建立班级",
    description: "创建班级并通过 CSV 或 XLSX 导入纯合成学生数据。",
    href: "/classes",
    icon: "classes",
  },
  {
    title: "创建作业",
    description: "填写基本信息、上传并整理试卷、设置已有题目的评分标准。",
    href: "/assignments",
    icon: "assignments",
  },
  {
    title: "批改与复核",
    description: "检查 OCR 结果，复核规则建议，并由教师确认最终分数。",
    href: "/grading",
    icon: "grading",
  },
  {
    title: "发布与分析",
    description: "固定成绩快照、生成报告并查看学情统计。",
    href: "/analytics",
    icon: "analytics",
  },
];

const questions = [
  {
    question: "为什么机器建议不能直接成为正式成绩？",
    answer:
      "AhaMark 把 OCR、规则评分和机器建议视为初批结果。只有教师复核并 finalize 后生成的最新合法 complete 成绩快照，才可进入正式发布和统计。",
  },
  {
    question: "缺交学生会按零分统计吗？",
    answer:
      "不会。缺交、未完成或未 finalized 的学生不记为零分，也不进入正式统计分母。",
  },
  {
    question: "系统可以识别手写数学和公式吗？",
    answer:
      "当前不能作可靠承诺。RapidOCR 只验证了有限的清晰印刷体场景；手写、公式、LaTeX、复杂表格和几何内容必须人工核对。",
  },
  {
    question: "可以使用真实学生数据吗？",
    answer:
      "不可以。当前项目等级为 C，仅限内部演示或开发测试，必须使用纯合成姓名、学号、试卷、答案和成绩。",
  },
];

export default function HelpPage() {
  return (
    <div className="space-y-8">
      <PageHeader
        title="使用帮助"
        description="了解教师端完整流程、常见问题和当前能力边界。"
        eyebrow={<DemoBadge />}
      />
      <section>
        <h2 className="mb-4 text-lg font-semibold">从哪里开始</h2>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {guides.map((guide, index) => (
            <Link
              key={guide.title}
              href={guide.href}
              className="rounded-xl border border-[var(--border)] bg-white p-5 shadow-[var(--shadow-sm)] transition hover:-translate-y-0.5 hover:border-[var(--brand-500)]"
            >
              <span className="flex items-center justify-between">
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--brand-50)] text-[var(--brand-700)]">
                  <Icon name={guide.icon} />
                </span>
                <span className="text-xs font-semibold text-slate-400">
                  0{index + 1}
                </span>
              </span>
              <h3 className="mt-4 font-semibold">{guide.title}</h3>
              <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                {guide.description}
              </p>
            </Link>
          ))}
        </div>
      </section>
      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(280px,.5fr)]">
        <Card className="p-5">
          <h2 className="text-lg font-semibold">常见问题</h2>
          <div className="mt-4 divide-y divide-[var(--border)]">
            {questions.map((item) => (
              <details key={item.question} className="group py-4">
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
        </Card>
        <Card className="h-fit p-5">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-amber-100 text-amber-800">
            <Icon name="help" />
          </span>
          <h2 className="mt-4 font-semibold">仍然遇到问题？</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
            请记录所在页面、操作步骤、页面提示和 request
            ID。不要发送密码、Cookie、CSRF、Session 或包含真实学生信息的截图。
          </p>
          <Link
            href="/settings"
            className="mt-4 inline-flex text-sm font-semibold text-[var(--brand-700)] hover:underline"
          >
            查看系统连接状态
          </Link>
        </Card>
      </section>
    </div>
  );
}
