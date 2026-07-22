export type Health = { status: string; service: string; version: string };
export type ApiErrorBody = {
  code: string;
  message: string;
  details: Record<string, unknown>;
  request_id: string;
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

function csrfToken(): string | undefined {
  if (typeof document === "undefined") return undefined;
  return document.cookie
    .split("; ")
    .find((item) => item.startsWith("ahamark_csrf="))
    ?.split("=")[1];
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
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
  if (!response.ok) {
    const body = (await response.json()) as ApiErrorBody;
    throw new ApiError(response.status, body);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export type AuthUser = { id: string; email: string; display_name: string };
export const authApi = {
  login: (email: string, password: string) =>
    request<AuthUser>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<AuthUser>("/auth/me"),
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
  due_at?: string;
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
  due_at?: string;
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
  region: (id: string, questionId: string, data: Record<string, unknown>) =>
    request<RegionRecord>(
      `/api/assignments/${id}/questions/${questionId}/regions`,
      { method: "POST", body: JSON.stringify(data) },
    ),
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
  matching: {
    total: number;
    confirmed: number;
    ambiguous: number;
    unmatched: number;
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
  upload: (batchId: string, files: File[]) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    return request<{ items: unknown[]; count: number }>(
      `/api/grading-batches/${batchId}/files`,
      { method: "POST", body },
    );
  },
  submissions: (batchId: string) =>
    request<unknown[]>(`/api/grading-batches/${batchId}/submissions`),
  correctAnswer: (
    answerId: string,
    data: { corrected_text?: string; corrected_latex?: string },
  ) =>
    request(`/api/student-answers/${answerId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  grade: (answerId: string) =>
    request(`/api/student-answers/${answerId}/grade`, { method: "POST" }),
  review: (answerId: string, data: Record<string, unknown>) =>
    request(`/api/student-answers/${answerId}/review`, {
      method: "PUT",
      body: JSON.stringify(data),
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
export const analyticsApi = {
  releases: (assignmentId: string) =>
    request<GradeRelease[]>(
      `/api/grade-releases?assignment_id=${assignmentId}`,
    ),
  readiness: (assignmentId: string, classId: string) =>
    request(
      `/api/assignments/${assignmentId}/classes/${classId}/grade-readiness`,
    ),
  createRelease: (data: Record<string, unknown>) =>
    request<GradeRelease>("/api/grade-releases", {
      method: "POST",
      body: JSON.stringify(data),
    }),
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
