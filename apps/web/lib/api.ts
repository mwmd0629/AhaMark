export type Health = { status: string; service: string; version: string };
export type ApiErrorBody = {
  code: string;
  message: string;
  details: Record<string, unknown>;
  request_id: string;
};

export type AssignmentReviewSessionRecord = {
  id: string;
  assignment_id: string;
  generation: number;
  draft_revision_id: string;
  paper_version_id: string;
  legacy_rubric_version_id: string | null;
  review_version: number;
  status: string;
  counts: { blocking: number; warning: number; info: number };
};

export type AssignmentReviewItemRecord = {
  id: string;
  section: string;
  entity_type: string;
  entity_id: string;
  severity: "blocking" | "warning" | "info";
  issue_code: string;
  title: string;
  message: string;
  evidence: Record<string, unknown>;
  source_hash: string;
  status: string;
  eligibility: boolean;
  teacher_action?: string | null;
  teacher_note?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
};

export type AssignmentReadinessRecord = {
  id: string;
  readiness_hash: string;
  status: string;
  expires_at: string;
  class_ids: string[];
  due_at: string | null;
  total_score: string;
  paper_version_id: string;
  legacy_rubric_version_id: string;
};

const reviewAction = (reviewVersion: number) => ({
  expected_review_version: reviewVersion,
  explicit_confirmation: true,
});

export const assignmentReviewApi = {
  list: (assignmentId: string) =>
    request<{ items: AssignmentReviewSessionRecord[] }>(
      `/api/assignments/${assignmentId}/review-sessions`,
    ),
  create: (assignmentId: string) =>
    request<AssignmentReviewSessionRecord>(
      `/api/assignments/${assignmentId}/review-sessions`,
      { method: "POST" },
    ),
  get: (sessionId: string) =>
    request<AssignmentReviewSessionRecord>(
      `/api/assignment-review-sessions/${sessionId}`,
    ),
  items: (sessionId: string) =>
    request<{ items: AssignmentReviewItemRecord[] }>(
      `/api/assignment-review-sessions/${sessionId}/items?limit=100`,
    ),
  refresh: (sessionId: string, reviewVersion: number) =>
    request<AssignmentReviewSessionRecord>(
      `/api/assignment-review-sessions/${sessionId}/refresh`,
      { method: "POST", body: JSON.stringify(reviewAction(reviewVersion)) },
    ),
  confirm: (sessionId: string, kind: string, reviewVersion: number) =>
    request<{ review_version: number }>(
      `/api/assignment-review-sessions/${sessionId}/confirm/${kind}`,
      { method: "POST", body: JSON.stringify(reviewAction(reviewVersion)) },
    ),
  disposition: (
    itemId: string,
    reviewVersion: number,
    action: "acknowledge" | "resolve_manual" | "reopen",
  ) =>
    request<{ review_version: number }>(
      `/api/assignment-review-items/${itemId}/disposition`,
      {
        method: "PATCH",
        body: JSON.stringify({
          expected_review_version: reviewVersion,
          action,
        }),
      },
    ),
  createBinding: (sessionId: string, reviewVersion: number) =>
    request<{ id: string; status: string; mapping: unknown[] }>(
      `/api/assignment-review-sessions/${sessionId}/rubric-binding`,
      { method: "POST", body: JSON.stringify(reviewAction(reviewVersion)) },
    ),
  confirmBinding: (bindingId: string, reviewVersion: number) =>
    request<{ review_version: number }>(
      `/api/assignment-rubric-publication-bindings/${bindingId}/confirm`,
      { method: "POST", body: JSON.stringify(reviewAction(reviewVersion)) },
    ),
  prepare: (sessionId: string, reviewVersion: number) =>
    request<AssignmentReadinessRecord>(
      `/api/assignment-review-sessions/${sessionId}/prepare-publication`,
      { method: "POST", body: JSON.stringify(reviewAction(reviewVersion)) },
    ),
  publish: (
    assignmentId: string,
    readiness: AssignmentReadinessRecord,
    expectedUpdatedAt: string,
  ) =>
    request<AssignmentRecord>(`/api/assignments/${assignmentId}/publish`, {
      method: "POST",
      body: JSON.stringify({
        readiness_snapshot_id: readiness.id,
        readiness_hash: readiness.readiness_hash,
        expected_assignment_updated_at: expectedUpdatedAt,
        explicit_confirmation: true,
      }),
    }),
};
export class ApiError extends Error {
  constructor(
    public status: number,
    public body: ApiErrorBody,
  ) {
    super(body.message);
  }
}
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function errorDetailMessage(detail: unknown): string | undefined {
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (!item || typeof item !== "object") return "";
        const record = item as Record<string, unknown>;
        const message = typeof record.msg === "string" ? record.msg.trim() : "";
        if (!message) return "";
        const location = Array.isArray(record.loc)
          ? record.loc
              .filter(
                (part) => typeof part === "string" || typeof part === "number",
              )
              .join(".")
          : "";
        return location ? `${location}：${message}` : message;
      })
      .filter(Boolean);
    return messages.length ? messages.join("；") : undefined;
  }
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (typeof record.message === "string" && record.message.trim()) {
      return record.message.trim();
    }
  }
  return undefined;
}

async function apiErrorBody(response: Response): Promise<ApiErrorBody> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = undefined;
  }
  const record =
    payload && typeof payload === "object" && !Array.isArray(payload)
      ? (payload as Record<string, unknown>)
      : undefined;
  const requestId =
    (typeof record?.request_id === "string" && record.request_id) ||
    response.headers.get("x-request-id") ||
    "";
  const message =
    (typeof record?.message === "string" && record.message.trim()) ||
    errorDetailMessage(record?.detail) ||
    response.statusText.trim() ||
    `请求失败（HTTP ${response.status}）`;
  const details =
    record?.details &&
    typeof record.details === "object" &&
    !Array.isArray(record.details)
      ? (record.details as Record<string, unknown>)
      : {};
  return {
    code:
      (typeof record?.code === "string" && record.code) ||
      `HTTP_${response.status}`,
    message,
    details,
    request_id: requestId,
  };
}

function csrfToken(): string | undefined {
  if (typeof document === "undefined") return undefined;
  return document.cookie
    .split("; ")
    .find((item) => item.startsWith("ahamark_csrf="))
    ?.split("=")[1];
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      cache: "no-store",
      credentials: "include",
      ...init,
      headers:
        init?.body instanceof FormData
          ? { "X-CSRF-Token": csrfToken() ?? "", ...init.headers }
          : {
              "Content-Type": "application/json",
              "X-CSRF-Token": csrfToken() ?? "",
              ...init?.headers,
            },
    });
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === "AbortError") {
      throw reason;
    }
    throw new ApiError(0, {
      code: "NETWORK_ERROR",
      message: "无法连接服务器，请检查网络或确认后端服务已启动。",
      details: {},
      request_id: "",
    });
  }
  if (!response.ok) {
    throw new ApiError(response.status, await apiErrorBody(response));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export type AuthUser = {
  id: string;
  email: string;
  display_name: string;
  must_change_password: boolean;
  roles: string[];
  active_student_link: boolean;
  landing_surface:
    "teacher" | "student" | "change_password" | "account_unavailable";
};
export const authApi = {
  login: (email: string, password: string) =>
    request<AuthUser>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<AuthUser>("/auth/me"),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<AuthUser>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
};
export async function getHealth(signal?: AbortSignal): Promise<Health> {
  return request<Health>("/health", { signal });
}

export type ClassRecord = {
  id: string;
  name: string;
  grade?: string;
  subject?: string;
  academic_year?: string;
  semester?: string;
  description?: string;
  status: "active" | "archived";
  student_count: number;
  active_student_count: number;
  group_count: number;
  created_at: string;
  updated_at: string;
};
export type Page<T> = {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
};
export type ClassInput = Pick<ClassRecord, "name"> &
  Partial<
    Pick<
      ClassRecord,
      "grade" | "subject" | "academic_year" | "semester" | "description"
    >
  >;
export const classesApi = {
  list: (query = "") =>
    request<Page<ClassRecord>>(`/api/classes${query ? `?${query}` : ""}`),
  get: (id: string) => request<ClassRecord>(`/api/classes/${id}`),
  create: (data: ClassInput) =>
    request<ClassRecord>("/api/classes", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: Partial<ClassInput>) =>
    request<ClassRecord>(`/api/classes/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  archive: (id: string) =>
    request<ClassRecord>(`/api/classes/${id}/archive`, { method: "POST" }),
  restore: (id: string) =>
    request<ClassRecord>(`/api/classes/${id}/restore`, { method: "POST" }),
};
export type Group = {
  id: string;
  name: string;
  description?: string;
  member_count: number;
};
export type Student = {
  id: string;
  name: string;
  student_number: string;
  gender?: string;
  email?: string;
  phone?: string;
  status: "active" | "archived";
  membership_status: "active" | "removed";
  joined_at: string;
  groups: Group[];
  assignment_history: [];
};
export const studentsApi = {
  list: (classId: string, query = "") =>
    request<Page<Student>>(
      `/api/classes/${classId}/students${query ? `?${query}` : ""}`,
    ),
  add: (
    classId: string,
    data: { name: string; student_number: string; email?: string },
  ) =>
    request<Student>(`/api/classes/${classId}/students`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (studentId: string, data: Partial<Student>) =>
    request<Student>(`/api/students/${studentId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  remove: (classId: string, studentId: string) =>
    request<{ status: string }>(
      `/api/classes/${classId}/students/${studentId}`,
      { method: "DELETE" },
    ),
};
export const groupsApi = {
  list: (classId: string) => request<Group[]>(`/api/classes/${classId}/groups`),
  create: (classId: string, data: { name: string; description?: string }) =>
    request<Group>(`/api/classes/${classId}/groups`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: { name: string; description?: string }) =>
    request<Group>(`/api/groups/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  remove: (id: string) =>
    request<void>(`/api/groups/${id}`, { method: "DELETE" }),
  setMembers: (id: string, student_ids: string[]) =>
    request<{ member_count: number }>(`/api/groups/${id}/members`, {
      method: "PUT",
      body: JSON.stringify({ student_ids }),
    }),
};
export type ImportPreview = {
  id: string;
  status: string;
  original_name: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows: number;
  confirmed_rows: number;
  result: Record<string, number>;
  rows: {
    row_number: number;
    status: string;
    data: Record<string, string>;
    errors: { field: string; code: string; message: string }[];
  }[];
};
export const importsApi = {
  templateUrl: (format: "xlsx" | "csv" = "xlsx") =>
    `${API_URL}/api/import-template?format=${format}`,
  preview: (classId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<ImportPreview>(`/api/classes/${classId}/imports`, {
      method: "POST",
      body,
    });
  },
  get: (id: string) => request<ImportPreview>(`/api/imports/${id}`),
  confirm: (id: string) =>
    request<ImportPreview>(`/api/imports/${id}/confirm`, { method: "POST" }),
};

export type AssignmentStatus =
  "draft" | "published" | "grading" | "completed" | "archived";
export type RegionRecord = {
  id: string;
  paper_page_id: string;
  x: string;
  y: string;
  width: string;
  height: string;
  source: "manual" | "ocr" | "ai";
};
export type QuestionRecord = {
  id: string;
  question_number: string;
  display_order: number;
  question_type: string;
  content_text?: string;
  max_score?: string;
  difficulty?: "easy" | "medium" | "hard";
  knowledge_points: { id: string; name: string }[];
  regions: RegionRecord[];
};
export type AssignmentRecord = {
  id: string;
  title: string;
  subject?: string;
  grade?: string;
  description?: string;
  instructions?: string;
  status: AssignmentStatus;
  total_score?: string;
  due_at?: string | null;
  published_at?: string;
  updated_at: string;
  classes: Pick<ClassRecord, "id" | "name" | "status">[];
  question_count?: number;
  paper_version?: {
    id: string;
    version: number;
    status: string;
    pages: {
      id: string;
      stored_file_id: string;
      file_name?: string;
      page_number: number;
      source_page_number?: number;
      width?: number;
      height?: number;
      rotation: 0 | 90 | 180 | 270;
      status: string;
    }[];
    questions: QuestionRecord[];
  };
  rubric_version?: {
    id: string;
    version: number;
    status: string;
    question_rubrics: {
      id: string;
      question_id: string;
      standard_answer?: string;
      scoring_notes?: string;
      items: { id: string; title: string; points: string }[];
    }[];
  };
  completeness: {
    ready: boolean;
    next_step: number;
    issues: {
      code: string;
      message: string;
      step: number;
      question_id?: string;
    }[];
  };
};
export type AssignmentInput = {
  title: string;
  subject?: string;
  grade?: string;
  description?: string;
  instructions?: string;
  total_score?: number;
  due_at?: string | null;
  class_ids: string[];
};
export const assignmentsApi = {
  list: (query = "") =>
    request<Page<AssignmentRecord>>(
      `/api/assignments${query ? `?${query}` : ""}`,
    ),
  get: (id: string) => request<AssignmentRecord>(`/api/assignments/${id}`),
  create: (data: AssignmentInput) =>
    request<AssignmentRecord>("/api/assignments", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: Partial<AssignmentInput>, updated_at: string) =>
    request<AssignmentRecord>(`/api/assignments/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ ...data, updated_at }),
    }),
  upload: (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<{ id: string; name: string; pages_created: number }>(
      `/api/assignments/${id}/files`,
      { method: "POST", body },
    );
  },
  preview: (id: string, fileId: string) =>
    request<{ url: string }>(`/api/assignments/${id}/files/${fileId}/preview`, {
      method: "POST",
    }),
  pagePreview: (id: string, pageId: string) =>
    request<{ url: string; width: number; height: number }>(
      `/api/assignments/${id}/pages/${pageId}/preview`,
      { method: "POST" },
    ),
  page: (id: string, pageId: string, data: Record<string, unknown>) =>
    request(`/api/assignments/${id}/pages/${pageId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  question: (id: string, data: Record<string, unknown>) =>
    request<QuestionRecord>(`/api/assignments/${id}/questions`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateQuestion: (
    id: string,
    questionId: string,
    data: Record<string, unknown>,
  ) =>
    request<QuestionRecord>(`/api/assignments/${id}/questions/${questionId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  region: (id: string, questionId: string, data: Record<string, unknown>) =>
    request<RegionRecord>(
      `/api/assignments/${id}/questions/${questionId}/regions`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  cutQuestion: (id: string, pageId: string, data: Record<string, unknown>) =>
    request<QuestionRecord>(
      `/api/assignments/${id}/pages/${pageId}/question-cuts`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  removeQuestion: (id: string, questionId: string) =>
    request<void>(`/api/assignments/${id}/questions/${questionId}`, {
      method: "DELETE",
    }),
  rubric: (id: string, questionId: string, data: Record<string, unknown>) =>
    request<AssignmentRecord>(`/api/assignments/${id}/rubrics/${questionId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  publishCheck: (id: string) =>
    request<AssignmentRecord["completeness"]>(
      `/api/assignments/${id}/publish-check`,
    ),
  publish: (id: string) =>
    request<AssignmentRecord>(`/api/assignments/${id}/publish`, {
      method: "POST",
    }),
  copy: (id: string) =>
    request<AssignmentRecord>(`/api/assignments/${id}/copy`, {
      method: "POST",
    }),
  archive: (id: string) =>
    request<AssignmentRecord>(`/api/assignments/${id}/archive`, {
      method: "POST",
    }),
};

export type RecognitionProviderStatus = {
  provider: string;
  version: string;
  available: boolean;
  demo: boolean;
  reason?: string;
  formula: { provider: string; available: boolean; reason?: string };
};
export type RecognitionJob = {
  id: string;
  paper_version_id: string;
  status:
    | "queued"
    | "running"
    | "completed"
    | "partially_completed"
    | "failed"
    | "cancelled";
  stage: string;
  progress: number;
  provider: string;
  error_code?: string;
  error_message?: string;
  page_summary: {
    total: number;
    completed: number;
    failed: number;
    stale: number;
  };
};
export type RecognitionPage = {
  id: string;
  paper_page_id: string;
  status: string;
  stage: string;
  progress: number;
  quality_score?: string;
  error_message?: string;
  rendered_url?: string;
  processed_url?: string;
  thumbnail_url?: string;
};
export type RecognitionCandidate = {
  id: string;
  temporary_number: string;
  question_type: string;
  content_text?: string;
  content_latex?: string;
  suggested_score?: string | null;
  confidence?: string;
  status: string;
  source: string;
  confirmed_question_id?: string;
  regions: {
    paper_page_id: string;
    x: string;
    y: string;
    width: string;
    height: string;
  }[];
};
export const recognitionApi = {
  providers: (assignmentId: string) =>
    request<RecognitionProviderStatus>(
      `/api/assignments/${assignmentId}/recognition/providers`,
    ),
  start: (assignmentId: string, paperVersionId: string) =>
    request<RecognitionJob>(
      `/api/assignments/${assignmentId}/recognition/jobs`,
      {
        method: "POST",
        body: JSON.stringify({
          paper_version_id: paperVersionId,
          idempotency_key: crypto.randomUUID(),
        }),
      },
    ),
  job: (assignmentId: string, jobId: string) =>
    request<RecognitionJob>(
      `/api/assignments/${assignmentId}/recognition/jobs/${jobId}`,
    ),
  pages: (assignmentId: string, jobId: string) =>
    request<RecognitionPage[]>(
      `/api/assignments/${assignmentId}/recognition/jobs/${jobId}/pages`,
    ),
  candidates: (assignmentId: string, jobId: string) =>
    request<RecognitionCandidate[]>(
      `/api/assignments/${assignmentId}/recognition/jobs/${jobId}/candidates`,
    ),
  patchCandidate: (
    assignmentId: string,
    jobId: string,
    candidateId: string,
    data: Record<string, unknown>,
  ) =>
    request<RecognitionCandidate>(
      `/api/assignments/${assignmentId}/recognition/jobs/${jobId}/candidates/${candidateId}`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),
  confirm: (assignmentId: string, jobId: string, candidateIds: string[]) =>
    request<{ created_question_ids: string[] }>(
      `/api/assignments/${assignmentId}/recognition/jobs/${jobId}/confirm`,
      {
        method: "POST",
        body: JSON.stringify({ candidate_ids: candidateIds }),
      },
    ),
  retryPage: (assignmentId: string, jobId: string, pageId: string) =>
    request<RecognitionJob>(
      `/api/assignments/${assignmentId}/recognition/jobs/${jobId}/pages/${pageId}/retry`,
      { method: "POST" },
    ),
};

export type GradingBatch = {
  id: string;
  assignment_id: string;
  class_id: string;
  name?: string;
  description?: string;
  status:
    | "draft"
    | "collecting"
    | "queued"
    | "running"
    | "grading"
    | "completed"
    | "failed"
    | "archived";
  submission_count: number;
  recognized_count: number;
  graded_count: number;
  reviewed_count: number;
  failed_count: number;
  workflow: {
    stage_counts: Record<string, number>;
    completed_count: number;
    blocked_count: number;
    blocked: Array<{
      stage: string;
      stage_label: string;
      reason_code?: string;
      reason: string;
      action: string;
      count: number;
    }>;
  };
  matching: {
    total: number;
    confirmed: number;
    ambiguous: number;
    unmatched: number;
    items: Array<{
      id: string;
      filename: string;
      status: string;
      method: string;
      reason?: string;
      suggested_student_id?: string;
      confirmed_student_id?: string;
    }>;
    student_options: Array<{
      id: string;
      student_number: string;
      name: string;
    }>;
  };
  actions: string[];
};
export const gradingApi = {
  batches: (assignmentId: string, query = "") =>
    request<Page<GradingBatch>>(
      `/api/assignments/${assignmentId}/grading-batches${query ? `?${query}` : ""}`,
    ),
  createBatch: (
    assignmentId: string,
    data: { class_id: string; name?: string; description?: string },
  ) =>
    request<GradingBatch>(`/api/assignments/${assignmentId}/grading-batches`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getBatch: (batchId: string) =>
    request<GradingBatch>(`/api/grading-batches/${batchId}`),
  upload: (batchId: string, files: File[]) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    return request<{ items: unknown[]; count: number }>(
      `/api/grading-batches/${batchId}/files`,
      { method: "POST", body },
    );
  },
  submissions: (batchId: string) =>
    request<SubmissionRecord[]>(`/api/grading-batches/${batchId}/submissions`),
  confirmMatch: (batchId: string, matchId: string, studentId: string) =>
    request<{ submission_id: string; status: string }>(
      `/api/grading-batches/${batchId}/matches/${matchId}/confirm`,
      {
        method: "POST",
        body: JSON.stringify({ student_id: studentId }),
      },
    ),
  undoUpload: (batchId: string, matchId: string) =>
    request(`/api/grading-batches/${batchId}/matches/${matchId}`, {
      method: "DELETE",
    }),
  startRecognition: (submissionId: string) =>
    request<SubmissionRecognitionJob>(
      `/api/submissions/${submissionId}/recognition-jobs`,
      {
        method: "POST",
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      },
    ),
  recognition: (submissionId: string, jobId: string) =>
    request<SubmissionRecognitionJob>(
      `/api/submissions/${submissionId}/recognition-jobs/${jobId}`,
    ),
  reorderPages: (submissionId: string, pageIds: string[]) =>
    request<{ submission_id: string; page_ids: string[] }>(
      `/api/submissions/${submissionId}/pages/order`,
      { method: "PUT", body: JSON.stringify({ page_ids: pageIds }) },
    ),
  splitSubmission: (submissionId: string, pageIds: string[]) =>
    request<{ source_submission_id: string; new_submission_id: string }>(
      `/api/submissions/${submissionId}/split`,
      { method: "POST", body: JSON.stringify({ page_ids: pageIds }) },
    ),
  mergeSubmission: (targetSubmissionId: string, sourceSubmissionId: string) =>
    request<{
      target_submission_id: string;
      source_submission_id: string;
      page_count: number;
    }>(`/api/submissions/${targetSubmissionId}/merge`, {
      method: "POST",
      body: JSON.stringify({ source_submission_id: sourceSubmissionId }),
    }),
  regrade: (batchId: string, onlyStale = false) =>
    request<{ count: number; grading_result_ids: string[] }>(
      `/api/grading-batches/${batchId}/regrade`,
      {
        method: "POST",
        body: JSON.stringify({ only_stale: onlyStale }),
      },
    ),
  correctAnswer: (
    answerId: string,
    data: { corrected_text?: string; corrected_latex?: string },
  ) =>
    request(`/api/student-answers/${answerId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  createAnswerRegion: (
    answerId: string,
    data: {
      submission_page_id: string;
      x: number;
      y: number;
      width: number;
      height: number;
      source?: "manual" | "template" | "ocr";
      confirmed?: boolean;
    },
  ) =>
    request(`/api/student-answers/${answerId}/regions`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  deleteAnswerRegion: (answerId: string, regionId: string) =>
    request(`/api/student-answers/${answerId}/regions/${regionId}`, {
      method: "DELETE",
    }),
  grade: (answerId: string) =>
    request(`/api/student-answers/${answerId}/grade`, { method: "POST" }),
  review: (answerId: string, data: Record<string, unknown>) =>
    request(`/api/student-answers/${answerId}/review`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  saveCodexSuggestion: (
    answerId: string,
    data: {
      score: string;
      reasoning: string;
      criterion_scores: Record<string, string>;
    },
  ) =>
    request(`/api/student-answers/${answerId}/codex-suggestion`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  bulkAcceptEligibility: (batchId: string) =>
    request<BulkAcceptEligibility>(
      `/api/grading-batches/${batchId}/bulk-accept-eligibility`,
    ),
  bulkAccept: (batchId: string, answerIds: string[]) =>
    request<{
      accepted_answer_ids: string[];
      excluded: Array<{ answer_id: string; reasons: string[] }>;
    }>(`/api/grading-batches/${batchId}/bulk-accept`, {
      method: "POST",
      body: JSON.stringify({ answer_ids: answerIds }),
    }),
  finalize: (submissionId: string) =>
    request(`/api/submissions/${submissionId}/finalize`, { method: "POST" }),
  snapshots: (assignmentId: string) =>
    request(`/api/assignments/${assignmentId}/score-snapshots?status=complete`),
  reviewWorkspace: (batchId: string) =>
    request<ReviewWorkspace>(
      `/api/grading-batches/${batchId}/review-workspace`,
    ),
};

export type BulkAcceptEligibility = {
  eligible_count: number;
  excluded_count: number;
  reason_counts: Record<string, number>;
  items: Array<{
    answer_id: string;
    eligible: boolean;
    reasons: string[];
  }>;
};

export type SubmissionRecord = {
  id: string;
  student_id?: string;
  status: string;
  attempt_number: number;
  page_count: number;
  workflow: {
    stage: string;
    stage_label: string;
    reason_code?: string;
    reason: string;
    action: string;
  };
};
export type SubmissionRecognitionJob = {
  id: string;
  submission_id: string;
  status: "queued" | "running" | "completed" | "partially_completed" | "failed";
  provider: string;
  provider_version: string;
  error_code?: string;
  error_message?: string;
  pages: Array<{
    id: string;
    page_number: number;
    status: string;
    rendered_storage_key?: string;
    processed_storage_key?: string;
    thumbnail_storage_key?: string;
  }>;
};

export type ReviewWorkspace = {
  batch: GradingBatch;
  progress: { total: number; reviewed: number };
  provider_notice: string;
  items: Array<{
    submission_id: string;
    student_id?: string;
    status: string;
    pages: Array<{
      id: string;
      page_number: number;
      status: string;
      original_url?: string;
      processed_url?: string;
      thumbnail_url?: string;
    }>;
    answers: Array<{
      id: string;
      status: string;
      recognized_text?: string;
      corrected_text?: string;
      effective_text?: string;
      confidence?: string;
      requires_review: boolean;
      question: {
        id: string;
        number: string;
        type: string;
        content?: string;
        max_score?: string;
      };
      result?: {
        id: string;
        status: string;
        rubric_version_id: string;
        score?: string;
        provider: string;
        provider_version: string;
        confidence?: string;
        requires_review: boolean;
        reasoning?: string;
      };
      review?: {
        decision: string;
        final_score?: string;
        feedback?: string;
        error_type?: string;
      };
      criteria: Array<{
        rubric_item_id: string;
        status: string;
        awarded_points?: string;
        max_points: string;
        reason?: string;
      }>;
      evidence: Array<{
        id: string;
        submission_page_id: string;
        quote?: string;
        x?: string;
        y?: string;
        width?: string;
        height?: string;
      }>;
      regions: Array<{
        id: string;
        submission_page_id: string;
        source: string;
        status: string;
        confidence?: string;
        x: string;
        y: string;
        width: string;
        height: string;
      }>;
    }>;
  }>;
};

export type GradeRelease = {
  id: string;
  assignment_id: string;
  class_id: string;
  version: number;
  status: "draft" | "scheduled" | "released" | "cancelled" | "superseded";
  release_mode: string;
  meaning: string;
  items: Array<{
    student_id: string;
    submission_id: string;
    score_snapshot_id: string;
  }>;
};
export type GradeReadiness = {
  releasable_count: number;
  unreleasable_count: number;
  ready: Array<{
    student_id: string;
    submission_id: string;
    score_snapshot_id: string;
  }>;
  errors: Array<Record<string, unknown>>;
  missing_student_ids: string[];
};
export type ReportJob = {
  id: string;
  report_type:
    "gradebook_xlsx" | "student_report_pdf" | "batch_student_reports";
  student_id?: string;
  status: string;
  progress: number;
  stored_file_id?: string;
  error_code?: string;
  grade_release_id: string;
  created_at?: string;
};
export const analyticsApi = {
  releases: (assignmentId: string) =>
    request<GradeRelease[]>(
      `/api/grade-releases?assignment_id=${assignmentId}`,
    ),
  readiness: (assignmentId: string, classId: string) =>
    request<GradeReadiness>(
      `/api/assignments/${assignmentId}/classes/${classId}/grade-readiness`,
    ),
  createRelease: (data: Record<string, unknown>) =>
    request<GradeRelease>("/api/grade-releases", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  createReport: (
    releaseId: string,
    reportType: ReportJob["report_type"],
    studentId?: string,
  ) => {
    const params = new URLSearchParams({
      report_type: reportType,
      idempotency_key: crypto.randomUUID(),
    });
    if (studentId) params.set("student_id", studentId);
    return request<ReportJob>(
      `/api/grade-releases/${releaseId}/reports?${params}`,
      { method: "POST" },
    );
  },
  reports: (releaseId: string) =>
    request<ReportJob[]>(`/api/grade-releases/${releaseId}/reports`),
  report: (jobId: string) => request<ReportJob>(`/api/report-jobs/${jobId}`),
  generate: (releaseId: string) =>
    request<{ id: string; metrics: Record<string, unknown> }>(
      `/api/grade-releases/${releaseId}/analytics`,
      { method: "POST" },
    ),
  insight: (analyticsId: string) =>
    request(`/api/analytics/${analyticsId}/insights`, { method: "POST" }),
  scoreBand: (snapshotId: string, band: string, page = 1) =>
    request<Page<Record<string, unknown>>>(
      `/api/analytics/${snapshotId}/score-bands/${band}/students?page=${page}`,
    ),
  question: (snapshotId: string, questionId: string, page = 1) =>
    request<Page<Record<string, unknown>>>(
      `/api/analytics/${snapshotId}/questions/${questionId}/students?page=${page}`,
    ),
  knowledgePoint: (snapshotId: string, knowledgePointId: string, page = 1) =>
    request<Page<Record<string, unknown>> & { scoring_rule: string }>(
      `/api/analytics/${snapshotId}/knowledge-points/${knowledgePointId}?page=${page}`,
    ),
  errorType: (snapshotId: string, errorType: string, page = 1) =>
    request<Page<Record<string, unknown>>>(
      `/api/analytics/${snapshotId}/errors/${encodeURIComponent(errorType)}?page=${page}`,
    ),
  classTrends: (classId: string) =>
    request<Page<Record<string, unknown>>>(
      `/api/classes/${classId}/analytics/trends`,
    ),
  student: (studentId: string) =>
    request<Record<string, unknown>>(`/api/students/${studentId}/analytics`),
  studentTrends: (studentId: string) =>
    request<Page<Record<string, unknown>>>(
      `/api/students/${studentId}/analytics/trends`,
    ),
  studentKnowledgeTrend: (studentId: string, knowledgePointId: string) =>
    request<Record<string, unknown>>(
      `/api/students/${studentId}/knowledge-points/${knowledgePointId}/trend`,
    ),
  classKnowledgeTrend: (classId: string, knowledgePointId: string) =>
    request<{ items: Record<string, unknown>[]; scoring_rule: string }>(
      `/api/classes/${classId}/knowledge-points/${knowledgePointId}/trend`,
    ),
  reportJobs: (studentId: string) =>
    request<Record<string, unknown>[]>(
      `/api/students/${studentId}/report-jobs`,
    ),
  editInsight: (insightId: string, recommendations: string[]) =>
    request(`/api/teaching-insights/${insightId}`, {
      method: "PATCH",
      body: JSON.stringify({ recommendations }),
    }),
  confirmInsight: (insightId: string) =>
    request(`/api/teaching-insights/${insightId}/confirm`, { method: "POST" }),
  regenerateInsight: (insightId: string) =>
    request(`/api/teaching-insights/${insightId}/regenerate`, {
      method: "POST",
    }),
  invalidateInsight: (insightId: string) =>
    request(`/api/teaching-insights/${insightId}/invalidate`, {
      method: "POST",
    }),
  retryReport: (jobId: string) =>
    request<Record<string, unknown>>(`/api/report-jobs/${jobId}/retry`, {
      method: "POST",
    }),
  reportDownload: (jobId: string) =>
    request<{ url: string }>(`/api/report-jobs/${jobId}/download`),
};

export type SubmissionProcessingJob = {
  id: string;
  submission_id: string;
  status:
    | "queued"
    | "running"
    | "completed"
    | "partially_completed"
    | "failed"
    | "cancelled";
  stage: string;
  progress: number;
  provider: string;
  provider_version: string;
  config_version: string;
  attempt: number;
  error_code?: string;
  error_message?: string;
};

export type SubmissionProcessingPage = {
  id: string;
  source_page_number: number;
  page_number: number;
  width?: number;
  height?: number;
  rotation: number;
  processing_status: string;
  preprocessing_version?: string;
  quality: {
    blur_score?: number;
    brightness?: number;
    contrast?: number;
    blank_probability?: number;
    duplicate_of_page_id?: string;
    orientation_confidence?: number;
    warnings: string[];
  };
  error_code?: string;
  retryable: boolean;
  original_url?: string;
  processed_url?: string;
  thumbnail_url?: string;
};

export type SubmissionRegionCandidate = {
  id: string;
  question_id: string;
  question_number?: string;
  student_answer_id: string;
  submission_page_id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  source: string;
  confidence?: number;
  status: "candidate" | "confirmed" | "rejected" | "manual_required" | "stale";
  reason?: string;
  segmentation_version: string;
};

export type SubmissionRegionMutation = {
  question_id: string;
  submission_page_id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  source: "manual" | "template" | "ocr" | "alignment";
  confidence?: number;
  status: "candidate" | "confirmed" | "rejected" | "manual_required";
  reason?: string;
};

export const submissionProcessingApi = {
  start: (submissionId: string) =>
    request<SubmissionProcessingJob>(
      `/api/submissions/${submissionId}/processing-jobs`,
      {
        method: "POST",
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      },
    ),
  job: (submissionId: string, jobId: string) =>
    request<SubmissionProcessingJob>(
      `/api/submissions/${submissionId}/processing-jobs/${jobId}`,
    ),
  pages: (submissionId: string) =>
    request<SubmissionProcessingPage[]>(
      `/api/submissions/${submissionId}/processing-pages`,
    ),
  regions: (submissionId: string) =>
    request<SubmissionRegionCandidate[]>(
      `/api/submissions/${submissionId}/region-candidates`,
    ),
  incomplete: (submissionId: string) =>
    request<{ complete: boolean; question_ids: string[] }>(
      `/api/submissions/${submissionId}/segmentation-incomplete`,
    ),
  addRegion: (submissionId: string, data: SubmissionRegionMutation) =>
    request<SubmissionRegionCandidate>(
      `/api/submissions/${submissionId}/region-candidates`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  updateRegion: (
    submissionId: string,
    regionId: string,
    data: SubmissionRegionMutation,
  ) =>
    request<SubmissionRegionCandidate>(
      `/api/submissions/${submissionId}/region-candidates/${regionId}`,
      { method: "PUT", body: JSON.stringify(data) },
    ),
  removeRegion: (submissionId: string, regionId: string) =>
    request<void>(
      `/api/submissions/${submissionId}/region-candidates/${regionId}`,
      { method: "DELETE" },
    ),
  confirmHighConfidence: (submissionId: string) =>
    request<{ confirmed_count: number }>(
      `/api/submissions/${submissionId}/region-candidates/confirm-high-confidence`,
      { method: "POST" },
    ),
  retryPage: (submissionId: string, jobId: string, pageId: string) =>
    request<SubmissionProcessingJob>(
      `/api/submissions/${submissionId}/processing-jobs/${jobId}/pages/${pageId}/retry`,
      { method: "POST" },
    ),
  rotatePage: (submissionId: string, pageId: string, degrees: -90 | 90 | 180) =>
    request<SubmissionProcessingJob>(
      `/api/submissions/${submissionId}/processing-pages/${pageId}/rotate`,
      { method: "POST", body: JSON.stringify({ degrees }) },
    ),
};

export type AnswerRecognitionBlock = {
  id: string;
  job_id: string;
  page_id: string;
  region_id?: string;
  source_page_number?: number;
  block_type: "text" | "formula" | "matrix" | "table" | "diagram" | "unknown";
  bbox: { x: number; y: number; width: number; height: number };
  reading_order: number;
  raw_text?: string;
  normalized_text?: string;
  latex?: string;
  confidence?: number;
  provider: string;
  provider_version: string;
  warning_codes: string[];
  requires_review: boolean;
  status: string;
  recognition_version: number;
  stale: boolean;
  confirmed_at?: string;
  evidence_image_url?: string;
};

export const answerRecognitionApi = {
  blocks: (submissionId: string) =>
    request<AnswerRecognitionBlock[]>(
      `/api/submissions/${submissionId}/recognition-blocks`,
    ),
  edit: (
    submissionId: string,
    blockId: string,
    data: Partial<
      Pick<
        AnswerRecognitionBlock,
        "raw_text" | "normalized_text" | "latex" | "block_type"
      >
    >,
  ) =>
    request<AnswerRecognitionBlock>(
      `/api/submissions/${submissionId}/recognition-blocks/${blockId}`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),
  split: (submissionId: string, blockId: string, offset: number) =>
    request<AnswerRecognitionBlock[]>(
      `/api/submissions/${submissionId}/recognition-blocks/${blockId}/split`,
      { method: "POST", body: JSON.stringify({ offset }) },
    ),
  merge: (submissionId: string, blockIds: string[]) =>
    request<AnswerRecognitionBlock>(
      `/api/submissions/${submissionId}/recognition-blocks/merge`,
      { method: "POST", body: JSON.stringify({ block_ids: blockIds }) },
    ),
  reorder: (submissionId: string, blockIds: string[]) =>
    request<{ block_ids: string[] }>(
      `/api/submissions/${submissionId}/recognition-blocks/order`,
      { method: "PUT", body: JSON.stringify({ block_ids: blockIds }) },
    ),
  confirm: (submissionId: string, answerId: string) =>
    request<{ status: string }>(
      `/api/submissions/${submissionId}/answers/${answerId}/recognition/confirm`,
      { method: "POST" },
    ),
  retry: (submissionId: string, regionId: string) =>
    request<{ job_id: string; status: string; generation: number }>(
      `/api/submissions/${submissionId}/regions/${regionId}/recognition/retry`,
      { method: "POST" },
    ),
};

export type StructuredCriterion = {
  id?: string;
  stable_key: string;
  title: string;
  description?: string;
  max_points: string;
  order?: number;
  criterion_type: string;
  required: boolean;
  dependencies: string[];
  validation_mode: "deterministic" | "manual_only";
  validation_rule: Record<string, unknown>;
};

export type StructuredRubric = {
  id: string;
  question_id: string;
  reference_answer_version_id: string;
  task_id?: string | null;
  rubric_version: number;
  title: string;
  total_points: string;
  status: "draft" | "confirmed" | "retired";
  criteria: StructuredCriterion[];
};

export type ReferenceAnswerVersion = {
  id: string;
  question_id: string;
  version: number;
  status: "draft" | "confirmed";
  source_type:
    | "teacher_authored"
    | "official_solution"
    | "imported_reference"
    | "ai_draft"
    | "other";
  raw_content: string;
  normalized_content: string;
  structured_content: Record<string, unknown>;
  provenance: Record<string, unknown>;
  content_hash: string;
};

export const structuredRubricApi = {
  references: (questionId: string) =>
    request<ReferenceAnswerVersion[]>(
      `/api/questions/${questionId}/reference-answers`,
    ),
  createReference: (
    questionId: string,
    data: Omit<
      ReferenceAnswerVersion,
      "id" | "question_id" | "version" | "status" | "content_hash"
    >,
  ) =>
    request<ReferenceAnswerVersion>(
      `/api/questions/${questionId}/reference-answers`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  updateReference: (
    referenceId: string,
    data: Omit<
      ReferenceAnswerVersion,
      "id" | "question_id" | "version" | "status" | "content_hash"
    >,
  ) =>
    request<ReferenceAnswerVersion>(`/api/reference-answers/${referenceId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  confirmReference: (referenceId: string) =>
    request<ReferenceAnswerVersion>(
      `/api/reference-answers/${referenceId}/confirm`,
      { method: "POST" },
    ),
  list: (questionId: string) =>
    request<StructuredRubric[]>(
      `/api/questions/${questionId}/structured-rubrics`,
    ),
  create: (
    questionId: string,
    data: Omit<
      StructuredRubric,
      "id" | "question_id" | "rubric_version" | "status"
    >,
  ) =>
    request<StructuredRubric>(
      `/api/questions/${questionId}/structured-rubrics`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  update: (
    rubricId: string,
    data: Omit<
      StructuredRubric,
      "id" | "question_id" | "rubric_version" | "status"
    >,
  ) =>
    request<StructuredRubric>(`/api/structured-rubrics/${rubricId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  validate: (rubricId: string) =>
    request<{ valid: boolean; errors: Array<{ code: string }> }>(
      `/api/structured-rubrics/${rubricId}/validate`,
      { method: "POST" },
    ),
  confirm: (rubricId: string) =>
    request<StructuredRubric>(`/api/structured-rubrics/${rubricId}/confirm`, {
      method: "POST",
    }),
  derive: (rubricId: string) =>
    request<StructuredRubric>(`/api/structured-rubrics/${rubricId}/derive`, {
      method: "POST",
    }),
  diff: (leftId: string, rightId: string) =>
    request<{
      left: StructuredRubric;
      right: StructuredRubric;
      changed_fields: string[];
    }>(`/api/structured-rubrics/${leftId}/diff/${rightId}`),
};

export type MathValidationJob = {
  id: string;
  status: string;
  stale: boolean;
  scoring_input_version: string;
  rubric_version_id: string;
  reference_answer_version_id: string;
  suggested_total: string;
  results: Array<{
    id: string;
    criterion_id: string;
    result: string;
    suggested_points?: string;
    comparison_method: string;
    evidence: Record<string, unknown>;
    diagnostics: Record<string, unknown>;
  }>;
};

export const mathValidationApi = {
  listForAnswer: (answerId: string) =>
    request<MathValidationJob[]>(
      `/api/student-answers/${answerId}/math-validation/jobs`,
    ),
  get: (jobId: string) =>
    request<MathValidationJob>(`/api/math-validation/jobs/${jobId}`),
  retry: (jobId: string, criterionId: string) =>
    request<MathValidationJob>(
      `/api/math-validation/jobs/${jobId}/criteria/${criterionId}/retry`,
      { method: "POST" },
    ),
};

export type AISuggestion = {
  id: string;
  criterion_id: string;
  criterion_stable_key: string;
  status:
    | "scored"
    | "abstain"
    | "manual"
    | "conflict"
    | "insufficient"
    | "failed"
    | "stale";
  reason?: string;
  error_codes: string[];
  evidence_ids: string[];
  validation_refs: string[];
  requires_review: true;
  suggested_points?: string;
  max_points: string;
  confidence?: string;
  evidence_refs: string[];
  missing_steps: string[];
  detected_errors: string[];
  manual_review_reason?: string;
  student_feedback?: string;
  teacher_note?: string;
  deterministic_conflict: boolean;
  review?: {
    id: string;
    action: "accepted" | "modified" | "rejected";
    selected_points?: string;
    reason: string;
    created_at: string;
  };
};

export type AIScoringJob = {
  id: string;
  student_answer_id: string;
  status: string;
  generation: number;
  provider: string;
  model?: string;
  prompt_version: string;
  schema_version: string;
  stale: boolean;
  error_code?: string;
  scoring_input_version: string;
  rubric_version_id: string;
  reference_answer_version_id: string;
  evidence: Array<{
    id: string;
    kind: "recognition" | "region";
    status: string;
    stale: boolean;
    version: number;
    confirmed_revision?: number;
    submission_page_id?: string;
    coordinates?: {
      x: string;
      y: string;
      width: string;
      height: string;
    };
    target_id: string;
  }>;
  validation?: {
    job_id: string;
    status: string;
    generation: number;
    stale: boolean;
    rubric_version_id: string;
    reference_answer_version_id: string;
    results: Array<{
      id: string;
      criterion_id: string;
      generation: number;
      result:
        | "verified"
        | "conflict"
        | "indeterminate"
        | "manual"
        | "manual_required"
        | "unsupported"
        | "failed";
      comparison_method: string;
      stale: boolean;
      diagnostics: Record<string, unknown>;
    }>;
  };
  usage: {
    input_tokens?: number;
    output_tokens?: number;
    images: number;
    estimated_cost?: string;
  };
  suggestions: AISuggestion[];
  feedback?: {
    student_feedback: string;
    teacher_summary: string;
    disposition: string;
  };
  invocations: Array<{
    provider: string;
    endpoint_mode: string;
    model?: string;
    request_id?: string;
    latency_ms?: number;
    status: string;
    error_code?: string;
    started_at?: string;
    completed_at?: string;
  }>;
};

export const aiGradingApi = {
  listForAnswer: (answerId: string) =>
    request<AIScoringJob[]>(`/api/ai-grading/student-answers/${answerId}/jobs`),
  create: (answerId: string, rubricVersionId: string) =>
    request<AIScoringJob>("/api/ai-grading/jobs", {
      method: "POST",
      body: JSON.stringify({
        student_answer_id: answerId,
        rubric_version_id: rubricVersionId,
        idempotency_key: `web:${answerId}:${crypto.randomUUID()}`,
      }),
    }),
  retry: (jobId: string) =>
    request<AIScoringJob>(`/api/ai-grading/jobs/${jobId}/retry`, {
      method: "POST",
    }),
  retryCriterion: (jobId: string, criterionKey: string) =>
    request<AIScoringJob>(
      `/api/ai-grading/jobs/${jobId}/criteria/${encodeURIComponent(criterionKey)}/retry`,
      { method: "POST" },
    ),
  cancel: (jobId: string) =>
    request<AIScoringJob>(`/api/ai-grading/jobs/${jobId}/cancel`, {
      method: "POST",
    }),
  review: (
    suggestionId: string,
    data: {
      action: "accepted" | "modified" | "rejected";
      selected_points?: number;
      reason: string;
    },
  ) =>
    request<{ id: string; action: string }>(
      `/api/ai-grading/suggestions/${suggestionId}/review`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  editFeedback: (
    jobId: string,
    data: { student_feedback: string; teacher_summary: string },
  ) =>
    request<{ status: string; published: boolean }>(
      `/api/ai-grading/jobs/${jobId}/feedback`,
      { method: "PUT", body: JSON.stringify(data) },
    ),
};

export type AssignmentGenerationStatus =
  | "queued"
  | "analyzing"
  | "processing_pages"
  | "extracting_questions"
  | "generating_rubrics"
  | "validating"
  | "review_required"
  | "ready"
  | "partial"
  | "failed"
  | "cancelled"
  | "stale";
export type AssignmentGenerationStage =
  | "analyzing"
  | "processing_pages"
  | "extracting_questions"
  | "generating_rubrics"
  | "validating";
export type AssignmentDraftRevision = {
  id: string;
  assignment_id: string;
  generation_job_id: string;
  revision: number;
  parent_revision_id?: string | null;
  source_snapshot_hash: string;
  status: string;
  draft_payload: Record<string, unknown>;
  risk_summary: { info: number; warning: number; blocking: number };
  teacher_edit_version: number;
  created_at: string;
  updated_at: string;
};
export type AssignmentGenerationJob = {
  id: string;
  assignment_id: string;
  generation: number;
  status: AssignmentGenerationStatus;
  current_stage?: AssignmentGenerationStage | null;
  progress: number;
  source_snapshot_hash: string;
  provider_mode: "unavailable" | "fake" | "openai_compatible";
  retryable: boolean;
  error_code?: string | null;
  error_message?: string | null;
  cancel_requested_at?: string | null;
  created_at: string;
  updated_at: string;
  reused?: boolean;
  revision?: AssignmentDraftRevision | null;
  stages: {
    id: string;
    stage: AssignmentGenerationStage;
    stage_generation: number;
    status: string;
    error_code?: string | null;
    result_payload: Record<string, unknown>;
    started_at?: string | null;
    completed_at?: string | null;
  }[];
  issues: {
    id: string;
    stage?: AssignmentGenerationStage | null;
    severity: "info" | "warning" | "blocking";
    code: string;
    message: string;
    resolution_status: string;
  }[];
};
export type AssignmentGenerationCapabilities = {
  enabled: boolean;
  provider: "unavailable" | "fake" | "openai_compatible";
  provider_status: "available" | "unavailable";
  provider_error_code?: string | null;
  external_provider_requests: boolean;
  teacher_start_allowed: boolean;
  suggestion_only: boolean;
  real_provider_quality_passed: boolean;
};
export type AnswerDraftCandidate = {
  id: string;
  question_id: string;
  question_version: string;
  candidate_version: number;
  source_type:
    | "teacher_official"
    | "publisher_official"
    | "teacher_provided"
    | "third_party"
    | "ai_generated"
    | "unknown";
  raw_content?: string | null;
  normalized_content?: string | null;
  structured_content: Record<string, unknown>;
  alternative_answers: Record<string, unknown>[];
  provenance: Record<string, unknown>;
  confidence: number;
  evidence: Record<string, unknown>[];
  warning_codes: string[];
  status: string;
  manual_required: boolean;
  teacher_edit_version: number;
  materialized_reference_answer_id?: string | null;
};
export type RubricCriterionDraft = {
  id: string;
  criterion_key: string;
  display_order: number;
  title: string;
  description?: string | null;
  points?: string | null;
  criterion_type: string;
  required: boolean;
  dependency_keys: string[];
  alternative_group?: string | null;
  partial_credit_rule: Record<string, unknown>;
  deduction_rule: Record<string, unknown>;
  validation_rule: Record<string, unknown>;
  common_error_codes: string[];
  feedback_template?: string | null;
  confidence: number;
  evidence: Record<string, unknown>[];
  manual_required: boolean;
};
export type RubricDraftCandidate = {
  id: string;
  question_id: string;
  question_version: string;
  answer_candidate_id: string;
  candidate_version: number;
  title: string;
  scoring_mode: "deterministic" | "ai_suggestion" | "hybrid" | "manual_only";
  total_points?: string | null;
  allow_partial_credit: boolean;
  domain_requirements: Record<string, unknown>;
  validation_config: Record<string, unknown>;
  common_error_types: Record<string, unknown>[];
  feedback_templates: Record<string, unknown>;
  confidence: number;
  evidence: Record<string, unknown>[];
  warning_codes: string[];
  status: string;
  manual_required: boolean;
  teacher_edit_version: number;
  materialized_structured_rubric_id?: string | null;
  criteria: RubricCriterionDraft[];
};
export type RubricDraftValidation = {
  id: string;
  status:
    | "verified"
    | "partially_verified"
    | "indeterminate"
    | "unsupported"
    | "failed"
    | "stale";
  validation_mode: string;
  deterministic_result: Record<string, unknown>;
  structural_result: Record<string, unknown>;
  issue_codes: string[];
  validator_version: string;
  completed_at?: string | null;
};

export type AssignmentFieldSuggestion = {
  id: string;
  field_name: string;
  suggested_value: unknown;
  normalized_value: unknown;
  confidence: number;
  evidence: { kind: string; reference_id: string; summary: string }[];
  suggestion_version: number;
  status:
    "suggested" | "accepted" | "modified" | "rejected" | "stale" | "superseded";
  teacher_value?: unknown;
  teacher_edit_version: number;
  review_note?: string;
};
export type AssignmentFileAnalysis = {
  id: string;
  stored_file_id: string;
  source_snapshot_hash: string;
  detected_mime_type: string;
  checksum: string;
  file_name?: string;
  file_size?: number;
  page_count?: number;
  suggested_role: string;
  role_confidence: number;
  suggested_answer_source: string;
  answer_source_confidence: number;
  duplicate_of_file_id?: string;
  analysis_status: string;
  evidence: { kind: string; reference_id: string; summary: string }[];
  warning_codes: string[];
  teacher_confirmed_role?: string;
  teacher_confirmed_answer_source?: string;
  teacher_edit_version: number;
};
export type AssignmentPageAnalysis = {
  id: string;
  paper_page_id: string;
  status: string;
  quality_score?: number;
  blank_probability?: number;
  missing_page_suspected: boolean;
  low_quality: boolean;
  corrupted: boolean;
  mixed_document_suspected: boolean;
  variant_label?: string;
  warning_codes: string[];
};
export type PageOrganizationSuggestion = {
  id: string;
  paper_version_id: string;
  paper_page_id: string;
  source_page_number?: number;
  current_page_number: number;
  current_rotation: number;
  current_status: string;
  suggested_page_number: number;
  suggested_rotation: number;
  suggested_status: string;
  confidence: number;
  reason_codes: string[];
  evidence: Record<string, unknown>[];
  status: string;
  teacher_edit_version: number;
};
export type QuestionExtractionRegion = {
  id: string;
  paper_page_id: string;
  display_order: number;
  region_type: string;
  x: number;
  y: number;
  width: number;
  height: number;
  confidence: number;
  evidence: Record<string, unknown>;
  cross_page_group?: string;
};
export type QuestionExtractionCandidate = {
  id: string;
  draft_revision_id: string;
  paper_version_id: string;
  candidate_version: number;
  parent_candidate_id?: string;
  question_number?: string;
  question_type: string;
  content_text?: string;
  content_latex?: string | null;
  max_score?: number | null;
  difficulty?: string;
  knowledge_point_suggestions: string[];
  field_confidences: Record<string, number>;
  overall_confidence: number;
  evidence: Record<string, unknown>;
  warning_codes: string[];
  status: string;
  manual_required: boolean;
  teacher_edit_version: number;
  materialized_question_id?: string;
  regions: QuestionExtractionRegion[];
  server_eligible: boolean;
};

export const assignmentGenerationApi = {
  capabilities: () =>
    request<AssignmentGenerationCapabilities>(
      "/api/assignment-generation-capabilities",
    ),
  listJobs: (assignmentId: string) =>
    request<AssignmentGenerationJob[]>(
      `/api/assignments/${assignmentId}/generation-jobs`,
    ),
  getJob: (jobId: string) =>
    request<AssignmentGenerationJob>(
      `/api/assignment-generation-jobs/${jobId}`,
    ),
  start: (
    assignmentId: string,
    data: {
      idempotency_key: string;
      expected_source_snapshot?: string;
    },
  ) =>
    request<AssignmentGenerationJob>(
      `/api/assignments/${assignmentId}/generation-jobs`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  cancel: (jobId: string) =>
    request<AssignmentGenerationJob>(
      `/api/assignment-generation-jobs/${jobId}/cancel`,
      { method: "POST" },
    ),
  retryStage: (jobId: string, stage: AssignmentGenerationStage) =>
    request<AssignmentGenerationJob>(
      `/api/assignment-generation-jobs/${jobId}/retry-stage`,
      { method: "POST", body: JSON.stringify({ stage }) },
    ),
  listRevisions: (assignmentId: string) =>
    request<AssignmentDraftRevision[]>(
      `/api/assignments/${assignmentId}/draft-revisions`,
    ),
  getRevision: (revisionId: string) =>
    request<AssignmentDraftRevision>(
      `/api/assignment-draft-revisions/${revisionId}`,
    ),
  patchMetadata: (
    revisionId: string,
    data: {
      expected_teacher_edit_version: number;
      label?: string;
      notes?: string;
    },
  ) =>
    request<AssignmentDraftRevision>(
      `/api/assignment-draft-revisions/${revisionId}/metadata`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),
  listFieldSuggestions: (revisionId: string) =>
    request<AssignmentFieldSuggestion[]>(
      `/api/assignment-draft-revisions/${revisionId}/field-suggestions`,
    ),
  dispositionField: (
    suggestionId: string,
    data: {
      action: "accept" | "modify" | "reject";
      expected_teacher_edit_version: number;
      expected_assignment_updated_at?: string;
      teacher_value?: unknown;
      review_note?: string;
    },
  ) =>
    request<AssignmentFieldSuggestion>(
      `/api/assignment-field-suggestions/${suggestionId}/disposition`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),
  confirmTotalScore: (
    suggestionId: string,
    data: {
      expected_teacher_edit_version: number;
      expected_assignment_updated_at: string;
      confirmed_value: number;
      explicit_confirmation: true;
      review_note?: string;
    },
  ) =>
    request<AssignmentFieldSuggestion>(
      `/api/assignment-field-suggestions/${suggestionId}/confirm-total-score`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  listFileAnalyses: (revisionId: string) =>
    request<AssignmentFileAnalysis[]>(
      `/api/assignment-draft-revisions/${revisionId}/file-analyses`,
    ),
  confirmFileAnalysis: (
    analysisId: string,
    data: {
      expected_teacher_edit_version: number;
      confirmed_role: string;
      confirmed_answer_source: string;
      review_note?: string;
    },
  ) =>
    request<AssignmentFileAnalysis>(
      `/api/assignment-source-file-analyses/${analysisId}/confirmation`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),
  listPageAnalyses: (analysisId: string) =>
    request<AssignmentPageAnalysis[]>(
      `/api/assignment-source-file-analyses/${analysisId}/pages`,
    ),
  listPageOrganization: (revisionId: string) =>
    request<PageOrganizationSuggestion[]>(
      `/api/assignment-draft-revisions/${revisionId}/page-organization-suggestions`,
    ),
  dispositionPageOrganization: (
    suggestionId: string,
    data: Record<string, unknown>,
  ) =>
    request<PageOrganizationSuggestion>(
      `/api/page-organization-suggestions/${suggestionId}/disposition`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),
  listQuestionCandidates: (revisionId: string) =>
    request<QuestionExtractionCandidate[]>(
      `/api/assignment-draft-revisions/${revisionId}/question-extraction-candidates`,
    ),
  dispositionQuestionCandidate: (
    candidateId: string,
    data: Record<string, unknown>,
  ) =>
    request<QuestionExtractionCandidate>(
      `/api/question-extraction-candidates/${candidateId}/disposition`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),
  acceptEligibleQuestions: (
    revisionId: string,
    data: Record<string, unknown>,
  ) =>
    request<{
      accepted_candidate_ids: string[];
      accepted_count: number;
      server_decided: true;
    }>(
      `/api/assignment-draft-revisions/${revisionId}/question-extraction-candidates/accept-eligible`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  listAnswerCandidates: (revisionId: string) =>
    request<AnswerDraftCandidate[]>(
      `/api/assignment-draft-revisions/${revisionId}/answer-draft-candidates`,
    ),
  dispositionAnswerCandidate: (
    candidateId: string,
    data: Record<string, unknown>,
  ) =>
    request<AnswerDraftCandidate>(
      `/api/answer-draft-candidates/${candidateId}/disposition`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),
  listRubricCandidates: (revisionId: string) =>
    request<RubricDraftCandidate[]>(
      `/api/assignment-draft-revisions/${revisionId}/rubric-draft-candidates`,
    ),
  dispositionRubricCandidate: (
    candidateId: string,
    data: Record<string, unknown>,
  ) =>
    request<RubricDraftCandidate>(
      `/api/rubric-draft-candidates/${candidateId}/disposition`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),
  rubricCandidateValidation: (candidateId: string) =>
    request<RubricDraftValidation[]>(
      `/api/rubric-draft-candidates/${candidateId}/validation`,
    ),
  acceptEligibleAnswers: (revisionId: string, data: Record<string, unknown>) =>
    request<{ accepted_ids: string[]; accepted_count: number }>(
      `/api/assignment-draft-revisions/${revisionId}/answer-draft-candidates/accept-eligible`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  acceptEligibleRubrics: (revisionId: string, data: Record<string, unknown>) =>
    request<{ accepted_ids: string[]; accepted_count: number }>(
      `/api/assignment-draft-revisions/${revisionId}/rubric-draft-candidates/accept-eligible`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  activate: (revisionId: string) =>
    request<AssignmentDraftRevision>(
      `/api/assignment-draft-revisions/${revisionId}/activate`,
      { method: "POST" },
    ),
};
