import Link from "next/link";
export default function Home() {
  return (
    <main className="grid min-h-screen place-items-center bg-white px-6">
      <div className="max-w-2xl text-center">
        <span className="mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-[var(--brand-600)] text-3xl font-black text-white">
          A
        </span>
        <p className="mt-6 text-sm font-bold uppercase tracking-[.22em] text-[var(--brand-700)]">
          教师与学生协作 · 教师最终把关
        </p>
        <h1 className="mt-4 text-4xl font-bold tracking-tight sm:text-6xl">
          让每一次批改，成为教学洞察。
        </h1>
        <p className="mx-auto mt-5 max-w-xl leading-7 text-[var(--text-secondary)]">
          AhaMark
          支持班级管理、作业提交、人工批改、错题复核、教学资源和成绩分析。 AI
          能力是可选增强项，关闭后不影响核心教学流程。
        </p>
        <Link
          href="/login"
          className="mt-8 inline-flex min-h-11 items-center rounded-xl bg-[var(--brand-600)] px-6 font-semibold text-white hover:bg-[var(--brand-700)]"
        >
          登录系统
        </Link>
      </div>
    </main>
  );
}
