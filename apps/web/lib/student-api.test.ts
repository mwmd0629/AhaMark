import { beforeEach, expect, it, vi } from "vitest";

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));

vi.mock("@/lib/api", () => ({ request: requestMock }));

import { studentApi, teachingResourcesApi } from "@/lib/student-api";

beforeEach(() => {
  requestMock.mockReset();
  window.sessionStorage.clear();
});

it("removes successful temporary uploads when another file upload fails", async () => {
  requestMock.mockImplementation((path: string, init?: RequestInit) => {
    if (path === "/api/student/submission-files" && init?.method === "POST") {
      const file = (init.body as FormData).get("file") as File;
      if (file.name === "bad.docx")
        return Promise.reject(new Error("unsupported"));
      return Promise.resolve({
        id: "stored-1",
        key: "student-submissions/user/stored-1.pdf",
        name: file.name,
        content_type: file.type,
        size: file.size,
        checksum: "checksum",
      });
    }
    if (
      path === "/api/student/submission-files/stored-1" &&
      init?.method === "DELETE"
    ) {
      return Promise.resolve(undefined);
    }
    return Promise.reject(new Error(`unexpected request: ${path}`));
  });

  await expect(
    studentApi.submitAssignment({ id: "assignment-1", class_id: "class-1" }, [
      new File(["pdf"], "answer.pdf", { type: "application/pdf" }),
      new File(["docx"], "bad.docx", {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      }),
    ]),
  ).rejects.toThrow("unsupported");

  expect(requestMock).toHaveBeenCalledWith(
    "/api/student/submission-files/stored-1",
    { method: "DELETE" },
  );
  expect(
    requestMock.mock.calls.some(([path]) =>
      String(path).includes("/submissions"),
    ),
  ).toBe(false);
});

it("does not return archived teaching resources to the teacher page", async () => {
  requestMock.mockResolvedValue([
    {
      id: "published",
      title: "讲义",
      resource_type: "handout",
      status: "published",
    },
    {
      id: "archived",
      title: "旧讲义",
      resource_type: "handout",
      status: "archived",
    },
  ]);

  await expect(teachingResourcesApi.list()).resolves.toMatchObject({
    items: [{ id: "published" }],
  });
});

it("reuses a pending idempotency key and removes uploads excluded by a replayed response", async () => {
  const finalizationKeys: string[] = [];
  let uploadNumber = 0;
  requestMock.mockImplementation((path: string, init?: RequestInit) => {
    if (path === "/api/student/submission-files" && init?.method === "POST") {
      uploadNumber += 1;
      return Promise.resolve({
        id: `new-upload-${uploadNumber}`,
        key: `student-submissions/user/new-upload-${uploadNumber}.pdf`,
        name: "answer.pdf",
        content_type: "application/pdf",
        size: 3,
        checksum: `checksum-${uploadNumber}`,
      });
    }
    if (path === "/api/student/assignments/assignment-1/submissions") {
      const body = JSON.parse(String(init?.body)) as {
        idempotency_key: string;
      };
      finalizationKeys.push(body.idempotency_key);
      if (finalizationKeys.length === 1) {
        return Promise.reject(new Error("response lost"));
      }
      return Promise.resolve({
        id: "original-submission",
        assignment_id: "assignment-1",
        class_id: "class-1",
        status: "uploaded",
        stored_file_ids: ["new-upload-1"],
      });
    }
    if (
      path === "/api/student/submission-files/new-upload-1" &&
      init?.method === "DELETE"
    ) {
      return Promise.reject(new Error("already attached"));
    }
    if (
      path?.startsWith("/api/student/submission-files/") &&
      init?.method === "DELETE"
    ) {
      return Promise.resolve(undefined);
    }
    return Promise.reject(new Error(`unexpected request: ${path}`));
  });
  const assignment = { id: "assignment-1", class_id: "class-1" };
  const file = new File(["pdf"], "answer.pdf", { type: "application/pdf" });

  await expect(studentApi.submitAssignment(assignment, [file])).rejects.toThrow(
    "response lost",
  );
  await expect(
    studentApi.submitAssignment(assignment, [file]),
  ).resolves.toMatchObject({
    id: "original-submission",
  });

  expect(finalizationKeys).toHaveLength(2);
  expect(finalizationKeys[1]).toBe(finalizationKeys[0]);
  expect(requestMock).toHaveBeenCalledWith(
    "/api/student/submission-files/new-upload-2",
    { method: "DELETE" },
  );
  expect(window.sessionStorage.length).toBe(0);
});
