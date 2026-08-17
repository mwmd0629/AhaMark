"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  assignmentsApi,
  classesApi,
  type ClassRecord,
  type JointExamTeam,
} from "@/lib/api";
import { Button, Card, Input, Select, useToast } from "@/components/ui";

export function JointExamTeamPanel({
  assignmentId,
  onChanged,
}: {
  assignmentId: string;
  onChanged?: () => void | Promise<void>;
}) {
  const toast = useToast();
  const [team, setTeam] = useState<JointExamTeam>();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [selectedClassId, setSelectedClassId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [nextTeam, ownedClasses] = await Promise.all([
        assignmentsApi.jointTeam(assignmentId),
        classesApi.list("status=active&page_size=100"),
      ]);
      setTeam(nextTeam);
      setClasses(ownedClasses.items);
      setError("");
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "无法加载联考团队",
      );
    }
  }, [assignmentId]);
  useEffect(() => void load(), [load]);
  const availableClasses = useMemo(() => {
    const linked = new Set(team?.classes.map((item) => item.id) ?? []);
    return classes.filter((item) => !linked.has(item.id));
  }, [classes, team?.classes]);
  async function finish(nextTeam: JointExamTeam, message: string) {
    setTeam(nextTeam);
    setSelectedClassId("");
    toast(message);
    await onChanged?.();
  }
  async function invite(form: FormData) {
    const email = String(form.get("email") ?? "").trim();
    if (!email) return;
    setBusy(true);
    try {
      await finish(
        await assignmentsApi.inviteJointCollaborator(assignmentId, email),
        "已邀请协作老师",
      );
    } catch (reason) {
      toast(reason instanceof ApiError ? reason.message : "邀请失败", "error");
    } finally {
      setBusy(false);
    }
  }
  async function authorizeClass() {
    if (!selectedClassId) return;
    setBusy(true);
    try {
      await finish(
        await assignmentsApi.authorizeJointClasses(assignmentId, [
          selectedClassId,
        ]),
        "班级已加入联考",
      );
    } catch (reason) {
      toast(reason instanceof ApiError ? reason.message : "加入失败", "error");
    } finally {
      setBusy(false);
    }
  }
  async function removeClass(classId: string) {
    setBusy(true);
    try {
      await finish(
        await assignmentsApi.removeJointClass(assignmentId, classId),
        "班级已移出联考",
      );
    } catch (reason) {
      toast(reason instanceof ApiError ? reason.message : "移除失败", "error");
    } finally {
      setBusy(false);
    }
  }
  if (error) return <Card className="p-5 text-sm text-red-700">{error}</Card>;
  if (!team)
    return (
      <Card className="p-5 text-sm text-slate-500">正在加载联考团队…</Card>
    );
  const editable = team.status === "draft";
  return (
    <Card className="space-y-4 p-5">
      <div>
        <strong>联考团队与班级</strong>
        <p className="mt-1 text-xs text-slate-500">
          主责老师邀请教师；各班负责人只授权自己名下的班级，不转移学生归属。
        </p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border p-3 text-sm">
          <span className="text-xs text-slate-500">主责老师</span>
          <p className="font-medium">{team.owner.display_name}</p>
          {team.owner.email && (
            <p className="text-xs text-slate-500">{team.owner.email}</p>
          )}
        </div>
        <div className="rounded-xl border p-3 text-sm">
          <span className="text-xs text-slate-500">协作老师</span>
          <p className="font-medium">
            {team.collaborators.length
              ? team.collaborators.map((item) => item.display_name).join("、")
              : "尚未邀请"}
          </p>
        </div>
      </div>
      {team.is_owner && editable && (
        <form action={invite} className="flex flex-wrap items-end gap-2">
          <Input
            className="min-w-64 flex-1"
            name="email"
            type="email"
            label="邀请教师邮箱"
            required
          />
          <Button loading={busy}>邀请</Button>
        </form>
      )}
      {editable && availableClasses.length > 0 && (
        <div className="flex flex-wrap items-end gap-2">
          <Select
            className="min-w-64 flex-1"
            label="授权我的班级加入"
            value={selectedClassId}
            onChange={(event) => setSelectedClassId(event.target.value)}
          >
            <option value="">请选择班级</option>
            {availableClasses.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}（{item.active_student_count} 人）
              </option>
            ))}
          </Select>
          <Button
            type="button"
            loading={busy}
            disabled={!selectedClassId}
            onClick={() => void authorizeClass()}
          >
            加入联考
          </Button>
        </div>
      )}
      <div className="space-y-2">
        {team.classes.map((item) => (
          <div
            key={item.id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-xl border p-3 text-sm"
          >
            <div>
              <strong>{item.name}</strong>
              <p className="text-xs text-slate-500">
                负责人：{item.owner_name}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <span
                className={`rounded-full px-2 py-1 text-xs ${
                  item.authorized
                    ? "bg-emerald-100 text-emerald-800"
                    : "bg-amber-100 text-amber-800"
                }`}
              >
                {item.authorized ? "已授权" : "待授权"}
              </span>
              {editable && (team.is_owner || item.mine) && (
                <Button
                  type="button"
                  variant="secondary"
                  loading={busy}
                  onClick={() => void removeClass(item.id)}
                >
                  移除
                </Button>
              )}
            </div>
          </div>
        ))}
        {!team.classes.length && (
          <p className="text-sm text-slate-500">尚未加入班级。</p>
        )}
      </div>
    </Card>
  );
}
