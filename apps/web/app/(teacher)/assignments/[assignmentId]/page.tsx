"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { Badge, Button, Card, PageHeader } from "@/components/ui";
import { assignmentsApi, type AssignmentRecord } from "@/lib/api";
import { formatQuestionScore } from "@/lib/question-score";
import { RecognitionWorkspace } from "@/components/recognition-workspace";

export default function AssignmentDetailPage({
  params,
}: {
  params: Promise<{ assignmentId: string }>;
}) {
  const { assignmentId } = use(params);
  const [item, setItem] = useState<AssignmentRecord>();
  useEffect(
    () => void assignmentsApi.get(assignmentId).then(setItem),
    [assignmentId],
  );
  if (!item) return <Card className="p-8">正在加载作业…</Card>;
  return (
    <div className="space-y-6">
      <PageHeader
        title={item.title}
        description="作业详情使用真实 API，不展示虚假提交或平均分。"
        actions={
          <>
            <Link href={`/assignments/${item.id}/edit`}>
              <Button variant="outline" disabled={item.status !== "draft"}>
                编辑
              </Button>
            </Link>
            <Button
              variant="outline"
              onClick={async () => {
                const copy = await assignmentsApi.copy(item.id);
                location.href = `/assignments/${copy.id}/edit`;
              }}
            >
              复制
            </Button>
            <Button
              variant="danger"
              onClick={async () =>
                setItem(await assignmentsApi.archive(item.id))
              }
            >
              归档
            </Button>
          </>
        }
      />
      <Card className="grid gap-4 p-6 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <small>状态</small>
          <br />
          <Badge status={item.status} />
        </div>
        <div>
          <small>班级</small>
          <p>{item.classes.map((x) => x.name).join("、")}</p>
        </div>
        <div>
          <small>总分 / 题目</small>
          <p>
            {item.total_score} / {item.paper_version?.questions.length ?? 0}
          </p>
        </div>
        <div>
          <small>版本</small>
          <p>
            试卷 v{item.paper_version?.version} · 评分 v
            {item.rubric_version?.version}
          </p>
        </div>
      </Card>
      {item.paper_version && item.status === "draft" && (
        <RecognitionWorkspace
          assignmentId={item.id}
          paperVersionId={item.paper_version.id}
        />
      )}
      <Card className="p-6">
        <h2 className="font-bold">题目</h2>
        <ol className="mt-3 grid gap-2">
          {item.paper_version?.questions.map((q) => (
            <li className="rounded-xl border p-3" key={q.id}>
              第 {q.question_number} 题 · {formatQuestionScore(q.max_score)} ·{" "}
              {q.content_text || "未填写题干"}
            </li>
          ))}
        </ol>
      </Card>
      <Card className="p-6">
        <h2 className="font-bold">学生提交与批改</h2>
        <p className="mt-2 text-sm text-slate-600">
          学生作业上传和 AI 初批尚未接入。
        </p>
        <Button className="mt-4" disabled>
          上传学生作业（后续开放）
        </Button>
      </Card>
    </div>
  );
}
