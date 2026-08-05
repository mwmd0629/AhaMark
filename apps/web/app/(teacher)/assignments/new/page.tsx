"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
} from "@/components/ui";
import {
  ApiError,
  assignmentsApi,
  classesApi,
  type ClassRecord,
} from "@/lib/api";

export default function NewAssignmentPage() {
  const router = useRouter();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [deliveryMode, setDeliveryMode] = useState<
    "class_assignment" | "joint_exam"
  >("class_assignment");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const loadClasses = useCallback(() => {
    setLoading(true);
    setError("");
    void classesApi
      .list("status=active&page_size=100")
      .then((result) => setClasses(result.items))
      .catch((reason) =>
        setError(
          reason instanceof ApiError
            ? reason.message
            : "无法加载班级，请稍后重试。",
        ),
      )
      .finally(() => setLoading(false));
  }, []);
  useEffect(loadClasses, [loadClasses]);
  if (loading) return <Card className="p-8">正在加载活动班级…</Card>;
  if (error) return <ErrorState description={error} retry={loadClasses} />;
  if (!classes.length)
    return (
      <div className="space-y-6">
        <PageHeader title="创建作业" description="先选择真实活动班级。" />
        <EmptyState
          title="没有活动班级"
          description="创建或恢复一个班级后才能布置作业。"
          action={
            <Link href="/classes">
              <Button>前往班级管理</Button>
            </Link>
          }
        />
      </div>
    );
  return (
    <div className="space-y-6">
      <PageHeader
        title="创建作业"
        description="选择班级并填写作业名称即可开始，课程信息会从班级继承。"
      />
      <Card className="p-6">
        <form
          className="grid max-w-2xl gap-4"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            const form = new FormData(e.currentTarget);
            try {
              const selectedClasses = classes.filter((entry) =>
                selected.includes(entry.id),
              );
              const sharedValue = (field: "subject" | "grade") => {
                const values = [
                  ...new Set(
                    selectedClasses
                      .map((entry) => entry[field]?.trim())
                      .filter((value): value is string => Boolean(value)),
                  ),
                ];
                return values.length === 1 ? values[0] : undefined;
              };
              const item = await assignmentsApi.create({
                title: String(form.get("title")),
                delivery_mode: deliveryMode,
                subject: sharedValue("subject"),
                grade: sharedValue("grade"),
                class_ids: selected,
              });
              router.push(`/assignments/${item.id}/edit?step=2`);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Input name="title" label="作业名称" required />
          <fieldset>
            <legend className="mb-2 text-sm font-medium">布置方式</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="rounded-xl border p-3">
                <input
                  type="radio"
                  name="delivery_mode"
                  checked={deliveryMode === "class_assignment"}
                  onChange={() => setDeliveryMode("class_assignment")}
                />{" "}
                <span className="ml-2 font-semibold">普通作业</span>
                <p className="ml-6 mt-1 text-xs text-slate-500">
                  可布置给一个或多个班级，按班批改。
                </p>
              </label>
              <label className="rounded-xl border p-3">
                <input
                  type="radio"
                  name="delivery_mode"
                  checked={deliveryMode === "joint_exam"}
                  onChange={() => setDeliveryMode("joint_exam")}
                />{" "}
                <span className="ml-2 font-semibold">联考统批</span>
                <p className="ml-6 mt-1 text-xs text-slate-500">
                  多班共用试卷和评分标准，统一查看进度，按班发布成绩。
                </p>
              </label>
            </div>
          </fieldset>
          <fieldset>
            <legend className="mb-2 text-sm font-medium">
              关联班级（可多选）
            </legend>
            <div className="grid gap-2">
              {classes.map((c) => (
                <label className="rounded-xl border p-3" key={c.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(c.id)}
                    onChange={(e) =>
                      setSelected((old) =>
                        e.target.checked
                          ? [...old, c.id]
                          : old.filter((id) => id !== c.id),
                      )
                    }
                  />{" "}
                  <span className="ml-2 font-semibold">{c.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
          {deliveryMode === "joint_exam" && selected.length < 2 && (
            <p className="text-sm text-blue-700">
              可先创建联考，再邀请其他老师授权其班级；发布前至少两个班级。
            </p>
          )}
          <Button
            loading={busy}
            disabled={deliveryMode !== "joint_exam" && !selected.length}
          >
            保存草稿并继续
          </Button>
        </form>
      </Card>
    </div>
  );
}
