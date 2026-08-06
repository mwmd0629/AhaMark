"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button, Card, EmptyState, Input, PageHeader } from "@/components/ui";
import { assignmentsApi, classesApi, type ClassRecord } from "@/lib/api";

export default function NewAssignmentPage() {
  const router = useRouter();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(
    () =>
      void classesApi
        .list("status=active&page_size=100")
        .then((x) => setClasses(x.items)),
    [],
  );
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
        description="填写最少信息即可保存后端草稿，随后进入三步向导。"
      />
      <Card className="p-6">
        <form
          className="grid max-w-2xl gap-4"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            const form = new FormData(e.currentTarget);
            try {
              const item = await assignmentsApi.create({
                title: String(form.get("title")),
                subject: String(form.get("subject")),
                grade: String(form.get("grade")),
                class_ids: selected,
              });
              router.push(`/assignments/${item.id}/edit`);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Input name="title" label="作业名称" required />
          <Input name="subject" label="学科" />
          <Input name="grade" label="年级" />
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
          <Button loading={busy} disabled={!selected.length}>
            保存草稿并继续
          </Button>
        </form>
      </Card>
    </div>
  );
}
