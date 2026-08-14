"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  assignmentsApi,
  gradingApi,
  type AssignmentRecord,
  type GradingBatch,
  type JointGradingPool,
  type JointGradingWork,
  type JointExamTeam,
} from "@/lib/api";
import { Button, Card, Input, PageHeader, Select } from "@/components/ui";
import { JointExamTeamPanel } from "@/components/joint-exam-team-panel";
import { useSmartRefresh } from "@/lib/use-smart-refresh";

export default function GradingPage() {
  const searchParams = useSearchParams();
  const requestedAssignmentId = searchParams.get("assignmentId") ?? "";
  const [assignments, setAssignments] = useState<AssignmentRecord[]>([]);
  const [assignmentId, setAssignmentId] = useState("");
  const [items, setItems] = useState<
    Array<GradingBatch & { class_name?: string }>
  >([]);
  const [jointPool, setJointPool] = useState<JointGradingPool>();
  const [invitations, setInvitations] = useState<JointExamTeam[]>([]);
  const [jointWork, setJointWork] = useState<JointGradingWork[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const assignment = useMemo(
    () => assignments.find((item) => item.id === assignmentId),
    [assignments, assignmentId],
  );

  useEffect(() => {
    assignmentsApi
      .list("page_size=100")
      .then((page) =>
        setAssignments(page.items.filter((item) => item.status !== "draft")),
      )
      .catch(() => setError("无法加载已发布作业"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    Promise.all([assignmentsApi.jointInvitations(), gradingApi.jointWork()])
      .then(([teams, work]) => {
        setInvitations(teams);
        setJointWork(work);
      })
      .catch((reason) =>
        setError(
          reason instanceof ApiError ? reason.message : "无法加载联考协作任务",
        ),
      );
  }, []);

  useEffect(() => {
    if (
      requestedAssignmentId &&
      assignments.some((item) => item.id === requestedAssignmentId)
    ) {
      setAssignmentId(requestedAssignmentId);
    }
  }, [assignments, requestedAssignmentId]);

  useEffect(() => {
    if (!assignmentId) {
      setItems([]);
      setJointPool(undefined);
      return;
    }
    setLoading(true);
    const selectedAssignment = assignments.find(
      (item) => item.id === assignmentId,
    );
    const request =
      selectedAssignment?.delivery_mode === "joint_exam"
        ? gradingApi.jointPool(assignmentId).then((pool) => {
            setJointPool(pool);
            return pool.items;
          })
        : gradingApi.batches(assignmentId).then((page) => {
            setJointPool(undefined);
            return page.items;
          });
    request
      .then(setItems)
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : "加载批次失败"),
      )
      .finally(() => setLoading(false));
  }, [assignmentId, assignments]);

  useSmartRefresh(
    async () => {
      const [assignmentPage, teams, work] = await Promise.all([
        assignmentsApi.list("page_size=100"),
        assignmentsApi.jointInvitations(),
        gradingApi.jointWork(),
      ]);
      const nextAssignments = assignmentPage.items.filter(
        (item) => item.status !== "draft",
      );
      setAssignments(nextAssignments);
      setInvitations(teams);
      setJointWork(work);
      if (!assignmentId) return;
      const selected = nextAssignments.find((item) => item.id === assignmentId);
      if (selected?.delivery_mode === "joint_exam") {
        const pool = await gradingApi.jointPool(assignmentId);
        setJointPool(pool);
        setItems(pool.items);
      } else {
        const page = await gradingApi.batches(assignmentId);
        setJointPool(undefined);
        setItems(page.items);
      }
    },
    { intervalMs: 60_000 },
  );

  async function createBatch(form: FormData) {
    if (!assignmentId) return;
    setLoading(true);
    setError("");
    try {
      const batch = await gradingApi.createBatch(assignmentId, {
        class_id: String(form.get("class_id")),
        name: String(form.get("name")),
      });
      setItems((old) => [batch, ...old]);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "创建批次失败");
    } finally {
      setLoading(false);
    }
  }

  async function ensureJointPool() {
    if (!assignmentId) return;
    setLoading(true);
    setError("");
    try {
      const pool = await gradingApi.ensureJointPool(assignmentId);
      setJointPool(pool);
      setItems(pool.items);
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "创建联考统批池失败",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="作业批改"
        description="选择作业，创建批次并检查结果。"
      />
      {invitations.map((team) => (
        <JointExamTeamPanel
          key={team.assignment_id}
          assignmentId={team.assignment_id}
          onChanged={async () => {
            setInvitations(await assignmentsApi.jointInvitations());
          }}
        />
      ))}
      {jointWork.length > 0 && (
        <Card className="space-y-3 p-5">
          <div>
            <strong>我的联考批改</strong>
            <p className="mt-1 text-xs text-slate-500">
              只显示分配给你的题目，不开放其他班级管理权限。
            </p>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            {jointWork.map((work) => (
              <div
                key={`${work.assignment_id}:${work.question_id}`}
                className="flex items-center justify-between gap-3 rounded-xl border p-3"
              >
                <div>
                  <strong className="text-sm">{work.assignment_title}</strong>
                  <p className="text-xs text-slate-500">
                    第 {work.question_number} 题 · {work.class_count} 个班 ·
                    已复核 {work.reviewed}/{work.total}
                  </p>
                </div>
                <Link
                  href={`/grading/${work.first_batch_id}/review?questionId=${work.question_id}&joint=1`}
                >
                  <Button>开始批改</Button>
                </Link>
              </div>
            ))}
          </div>
        </Card>
      )}
      <Card className="space-y-4 p-5">
        <Select
          label="已发布作业"
          value={assignmentId}
          onChange={(event) => setAssignmentId(event.target.value)}
        >
          <option value="">请选择作业</option>
          {assignments.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </Select>
        {assignment?.delivery_mode === "joint_exam" && (
          <div className="rounded-xl border border-blue-200 bg-blue-50 p-4">
            <strong className="text-sm text-blue-900">联考统批</strong>
            <p className="mt-1 text-xs text-blue-800">
              {assignment.classes.length}{" "}
              个班级共用一套试卷与评分标准；系统按班接收答卷，统一汇总批改进度。
            </p>
            <Button
              className="mt-3"
              type="button"
              loading={loading}
              onClick={() => void ensureJointPool()}
            >
              {items.length ? "补齐联考批次" : "创建联考统批池"}
            </Button>
          </div>
        )}
        {assignment && assignment.delivery_mode !== "joint_exam" && (
          <form action={createBatch} className="grid gap-3 md:grid-cols-3">
            <Select name="class_id" label="班级" required defaultValue="">
              <option value="" disabled>
                请选择班级
              </option>
              {assignment.classes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
            <Input name="name" label="批次名称" required />
            <Button className="mt-6" loading={loading}>
              创建批改批次
            </Button>
          </form>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
      </Card>
      {assignment?.delivery_mode === "joint_exam" &&
        jointPool &&
        jointPool.questions.length > 0 && (
          <Card className="space-y-3 p-5">
            <div>
              <strong>按题跨班统批</strong>
              <p className="mt-1 text-xs text-slate-500">
                选择一道题后，系统会按班级顺序连续进入下一份答案。
              </p>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {jointPool.questions.map((question) => (
                <div
                  key={question.id}
                  className="flex items-center justify-between rounded-xl border p-3"
                >
                  <div>
                    <strong className="text-sm">第 {question.number} 题</strong>
                    <p className="text-xs text-slate-500">
                      已复核 {question.reviewed}/{question.total}
                    </p>
                  </div>
                  {jointPool.items[0] && (
                    <Link
                      href={`/grading/${jointPool.items[0].id}/review?questionId=${question.id}&joint=1`}
                    >
                      <Button>开始统批</Button>
                    </Link>
                  )}
                </div>
              ))}
            </div>
          </Card>
        )}
      <div className="grid gap-3">
        {items.map((batch) => (
          <Card
            key={batch.id}
            className="p-5"
            data-testid="grading-batch"
            data-batch-id={batch.id}
          >
            <div>
              <strong>{batch.name || "未命名批次"}</strong>
              {batch.class_name && (
                <span className="ml-2 rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">
                  {batch.class_name}
                </span>
              )}
              <p className="mt-1 text-xs text-slate-500">
                共 {batch.submission_count} 份 · 已复核 {batch.reviewed_count}{" "}
                份
              </p>
            </div>
            {batch.matching.unmatched > 0 && (
              <p className="mt-3 text-xs text-amber-700">
                {batch.matching.unmatched} 份待匹配
              </p>
            )}
            <Link href={`/grading/${batch.id}`}>
              <Button className="mt-4">打开批次</Button>
            </Link>
          </Card>
        ))}
      </div>
      {!loading && assignmentId && items.length === 0 && (
        <Card className="p-5 text-sm text-slate-500">
          {assignment?.delivery_mode === "joint_exam"
            ? "尚未创建联考统批池。"
            : "暂无批次，请通过上方表单创建。"}
        </Card>
      )}
    </div>
  );
}
