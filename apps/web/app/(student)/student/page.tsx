"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { studentPortalApi, type StudentPortalAssignment } from "@/lib/api";

export default function StudentHomePage() {
  const [profile, setProfile] = useState<{
    profiles: Array<{ name: string; student_number: string }>;
  }>();
  const [assignments, setAssignments] = useState<StudentPortalAssignment[]>();
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([studentPortalApi.me(), studentPortalApi.assignments()])
      .then(([nextProfile, nextAssignments]) => {
        setProfile(nextProfile);
        setAssignments(nextAssignments);
      })
      .catch(() => setError("无法加载学生成绩，请确认账号已关联学生档案。"));
  }, []);

  if (error)
    return <Card className="border-red-300 p-5 text-red-700">{error}</Card>;
  if (!profile || !assignments) return <Skeleton className="h-64 w-full" />;
  const primary = profile.profiles[0];
  return (
    <div className="space-y-6">
      <PageHeader
        title={primary ? `${primary.name}的作业` : "我的作业"}
        description={
          primary
            ? `学号 ${primary.student_number} · 这里只显示教师已正式向学生开放的成绩。`
            : "这里只显示教师已正式向学生开放的成绩。"
        }
      />
      {!assignments.length ? (
        <EmptyState
          icon="assignments"
          title="暂无已开放成绩"
          description="教师确认成绩但尚未向学生开放时，这里不会提前显示。"
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {assignments.map((assignment) => (
            <Link
              key={`${assignment.release_id}-${assignment.student_id}`}
              href={`/student/${assignment.release_id}`}
              className="rounded-2xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
            >
              <Card className="h-full p-5 transition hover:border-blue-300 hover:shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-bold">
                      {assignment.assignment_title}
                    </h2>
                    <p className="mt-1 text-sm text-slate-600">
                      {assignment.class_name}
                      {assignment.subject ? ` · ${assignment.subject}` : ""}
                    </p>
                  </div>
                  <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">
                    第 {assignment.release_version} 版
                  </span>
                </div>
                <p className="mt-5 text-sm font-medium text-blue-700">
                  查看正式成绩 →
                </p>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
