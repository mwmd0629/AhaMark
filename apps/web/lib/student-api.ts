import { request, type Page } from "@/lib/api";

export type CollectionResponse<T> = Page<T> | { items: T[] } | T[];

export function collectionItems<T>(response: CollectionResponse<T>): T[] {
  if (Array.isArray(response)) return response;
  return response.items;
}

export type StudentProfile = {
  id: string;
  name: string;
  display_name?: string;
  student_number?: string;
  email?: string;
  classes?: Array<{ id: string; name: string }>;
  profiles?: Array<{
    student_id: string;
    student_number: string;
    name: string;
    teacher_id: string;
  }>;
};

export type StudentAssignment = {
  id: string;
  class_id: string;
  class_name?: string;
  title: string;
  subject?: string;
  instructions?: string;
  due_at?: string | null;
  published_at?: string | null;
  status: string;
  submission_status?: string | null;
  submission_id?: string | null;
  submitted_at?: string | null;
  allowed_file_types?: string[];
  max_files?: number;
  submission?: StudentSubmission | null;
};

export type StudentSubmission = {
  id: string;
  assignment_id: string;
  class_id: string;
  status: string;
  submitted_at?: string | null;
  stored_file_ids?: string[];
};

export type StudentResult = {
  id: string;
  assignment_id: string;
  assignment_title: string;
  subject?: string;
  score: number | string | null;
  total_score: number | string | null;
  released_at: string;
  release_version?: number;
  teacher_comment?: string | null;
  wrong_question_count?: number;
};

export type WrongQuestion = {
  id: string;
  answer_id: string;
  assignment_id: string;
  assignment_title: string;
  question_number?: string;
  question_text: string;
  student_answer?: string | null;
  correct_answer?: string | null;
  score?: number | string | null;
  max_score?: number | string | null;
  error_reason?: string | null;
  knowledge_points?: string[];
  thread_id?: string | null;
  thread_status?: string | null;
  review_status?: string | null;
  review_request_id?: string | null;
  review_decision?: string | null;
  teacher_response?: string | null;
  released_at?: string;
};

export type WrongQuestionThread = {
  id: string;
  student_answer_id: string;
  status: string;
};

export type WrongQuestionMessage = {
  id: string;
  thread_id: string;
  role: "student" | "assistant" | "teacher" | "system";
  content: string;
  created_at: string;
  status?: string;
  verdict?: "likely_student_error" | "likely_ai_misjudgment" | "uncertain";
  requires_teacher_review?: boolean;
};

export type TeacherReviewSubmission = {
  id: string;
  status: string;
};

export type StudentLearningAnalysis = {
  id: string;
  status: string;
  generated_at?: string | null;
  strengths?: string[];
  weaknesses?: string[];
  knowledge_gaps?: string[];
  recommended_actions?: string[];
  summary?: string | null;
  source_release_count?: number;
};

export type TeachingResource = {
  id: string;
  title: string;
  description?: string | null;
  resource_type: "ppt" | "handout" | "reference" | "link" | string;
  subject?: string | null;
  class_id?: string | null;
  class_name?: string | null;
  url?: string | null;
  download_url?: string | null;
  original_name?: string | null;
  status?: "draft" | "published" | "archived" | string;
  published_at?: string | null;
  created_at?: string;
  stored_file_id?: string | null;
};

export type StoredUpload = {
  id: string;
  key: string;
  name: string;
  content_type: string;
  size: number;
  checksum: string;
};

export type TeacherReviewRequest = {
  id: string;
  student_id: string;
  student_name?: string;
  assignment_id?: string | null;
  assignment_title?: string;
  question_id?: string | null;
  question_number?: string;
  question?: string;
  student_answer?: string | null;
  submission_id?: string | null;
  grading_batch_id?: string | null;
  score_snapshot_version?: number | null;
  published_score?: number | string | null;
  published_max_score?: number | string | null;
  published_feedback?: string | null;
  published_error_type?: string | null;
  conversation_summary?: string | null;
  ai_verdict?: string | null;
  status:
    "pending" | "reviewing" | "confirmed" | "revised" | "rejected" | string;
  teacher_note?: string | null;
  created_at?: string;
  resolved_at?: string | null;
  student_question?: string;
  teacher_response?: string | null;
  decision?: string | null;
  submitted_at?: string;
  student_answer_id?: string;
};

function uploadFile(
  assignment: Pick<StudentAssignment, "id" | "class_id">,
  file: File,
) {
  const body = new FormData();
  body.append("file", file);
  body.append("assignment_id", assignment.id);
  body.append("class_id", assignment.class_id);
  return request<StoredUpload>("/api/student/submission-files", {
    method: "POST",
    body,
  });
}

function deleteSubmissionFile(fileId: string) {
  return request<void>(`/api/student/submission-files/${fileId}`, {
    method: "DELETE",
  });
}

async function cleanupSubmissionFiles(files: StoredUpload[]) {
  await Promise.allSettled(files.map((file) => deleteSubmissionFile(file.id)));
}

const submissionIdempotencyPrefix = "ahamark.student-submission";

function submissionIdempotencyStorageKey(
  assignment: Pick<StudentAssignment, "id" | "class_id">,
) {
  return `${submissionIdempotencyPrefix}.${assignment.class_id}.${assignment.id}`;
}

function pendingSubmissionIdempotencyKey(
  assignment: Pick<StudentAssignment, "id" | "class_id">,
) {
  const generated =
    typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  if (typeof window === "undefined") return generated;
  try {
    const key = submissionIdempotencyStorageKey(assignment);
    const existing = window.sessionStorage.getItem(key);
    if (existing) return existing;
    window.sessionStorage.setItem(key, generated);
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
  return generated;
}

function clearSubmissionIdempotencyKey(
  assignment: Pick<StudentAssignment, "id" | "class_id">,
  expected: string,
) {
  if (typeof window === "undefined") return;
  try {
    const key = submissionIdempotencyStorageKey(assignment);
    if (window.sessionStorage.getItem(key) === expected) {
      window.sessionStorage.removeItem(key);
    }
  } catch {
    // A successful submission must not be reported as failed because storage is unavailable.
  }
}

export const studentApi = {
  me: async () => {
    const response = await request<{
      user_id: string;
      email: string;
      profiles: Array<{
        student_id: string;
        student_number: string;
        name: string;
        teacher_id: string;
      }>;
    }>("/api/student/me");
    const primary = response.profiles[0];
    return {
      id: primary?.student_id ?? response.user_id,
      name: primary?.name ?? "同学",
      student_number: primary?.student_number,
      email: response.email,
      profiles: response.profiles,
    } satisfies StudentProfile;
  },
  assignments: () =>
    request<
      CollectionResponse<
        StudentAssignment & { submission?: StudentSubmission | null }
      >
    >("/api/student/assignments").then((response) => ({
      items: collectionItems(response).map((item) => ({
        ...item,
        submission_id: item.submission?.id ?? null,
        submission_status: item.submission?.status ?? null,
        submitted_at: item.submission?.submitted_at ?? null,
      })),
    })),
  submitAssignment: async (
    assignment: Pick<StudentAssignment, "id" | "class_id">,
    files: File[],
  ) => {
    const idempotencyKey = pendingSubmissionIdempotencyKey(assignment);
    const uploadResults = await Promise.allSettled(
      files.map((file) => uploadFile(assignment, file)),
    );
    const uploaded = uploadResults.flatMap((result) =>
      result.status === "fulfilled" ? [result.value] : [],
    );
    const uploadFailure = uploadResults.find(
      (result): result is PromiseRejectedResult => result.status === "rejected",
    );
    if (uploadFailure) {
      await cleanupSubmissionFiles(uploaded);
      throw uploadFailure.reason;
    }
    try {
      const submission = await request<StudentSubmission>(
        `/api/student/assignments/${assignment.id}/submissions`,
        {
          method: "POST",
          body: JSON.stringify({
            class_id: assignment.class_id,
            stored_file_ids: uploaded.map((item) => item.id),
            idempotency_key: idempotencyKey,
          }),
        },
      );
      if (Array.isArray(submission.stored_file_ids)) {
        const attached = new Set(submission.stored_file_ids);
        await cleanupSubmissionFiles(
          uploaded.filter((file) => !attached.has(file.id)),
        );
      }
      clearSubmissionIdempotencyKey(assignment, idempotencyKey);
      return submission;
    } catch (reason) {
      await cleanupSubmissionFiles(uploaded);
      throw reason;
    }
  },
  deleteSubmissionFile,
  results: async () => {
    const response = await request<
      CollectionResponse<{
        grade_release_id: string;
        grade_release_version: number;
        released_at: string;
        assignment_id: string;
        assignment_title: string;
        total_score: string | null;
        max_score: string;
        details?: Array<{ score?: string; max_score?: string }>;
      }>
    >("/api/student/results");
    return {
      items: collectionItems(response).map((item) => ({
        id: item.grade_release_id,
        assignment_id: item.assignment_id,
        assignment_title: item.assignment_title,
        score: item.total_score,
        total_score: item.max_score,
        released_at: item.released_at,
        release_version: item.grade_release_version,
        wrong_question_count: (item.details ?? []).filter((detail) => {
          const score = Number(detail.score);
          const maximum = Number(detail.max_score);
          return (
            Number.isFinite(score) &&
            Number.isFinite(maximum) &&
            score < maximum
          );
        }).length,
      })),
    };
  },
  wrongQuestions: async () => {
    const response = await request<
      CollectionResponse<{
        student_answer_id: string;
        assignment_id: string;
        assignment_title: string;
        question_number?: string;
        question_content?: string | null;
        score?: string;
        max_score?: string;
        feedback?: string | null;
        error_type?: string | null;
        thread_id?: string | null;
        thread_status?: string | null;
        student_answer?: string | null;
        review_request_id?: string | null;
        review_status?: string | null;
        review_decision?: string | null;
        teacher_response?: string | null;
      }>
    >("/api/student/wrong-questions");
    return {
      items: collectionItems(response).map((item) => ({
        id: item.student_answer_id,
        answer_id: item.student_answer_id,
        assignment_id: item.assignment_id,
        assignment_title: item.assignment_title,
        question_number: item.question_number,
        question_text:
          item.question_content || "题目文字暂不可用，请结合原作业查看。",
        score: item.score,
        max_score: item.max_score,
        student_answer: item.student_answer,
        error_reason: item.feedback || item.error_type,
        thread_id: item.thread_id,
        thread_status: item.thread_status,
        review_request_id: item.review_request_id,
        review_status: item.review_status,
        review_decision: item.review_decision,
        teacher_response: item.teacher_response,
      })),
    };
  },
  createWrongQuestionThread: (answerId: string) =>
    request<WrongQuestionThread>(
      `/api/student/wrong-questions/${answerId}/threads`,
      { method: "POST" },
    ),
  messages: async (threadId: string) => {
    const response = await request<
      CollectionResponse<
        Omit<
          WrongQuestionMessage,
          "thread_id" | "verdict" | "requires_teacher_review"
        > & {
          structured_payload?: Record<string, unknown>;
        }
      >
    >(`/api/student/wrong-question-threads/${threadId}/messages`);
    return {
      items: collectionItems(response).map((message) => ({
        ...message,
        thread_id: threadId,
        verdict: message.structured_payload
          ?.verdict as WrongQuestionMessage["verdict"],
        requires_teacher_review:
          message.structured_payload?.requires_teacher_review === true,
      })),
    };
  },
  askAI: (threadId: string, content: string) =>
    request<{
      message: Omit<WrongQuestionMessage, "thread_id">;
      job: { id: string; status: string; generation: number };
    }>(`/api/student/wrong-question-threads/${threadId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  aiJob: (jobId: string) =>
    request<{
      id: string;
      status: string;
      retryable: boolean;
      error_code?: string | null;
      reply?: WrongQuestionMessage | null;
    }>(`/api/student/ai-jobs/${jobId}`),
  requestTeacherReview: (threadId: string, question: string) =>
    request<TeacherReviewSubmission>(
      `/api/student/wrong-question-threads/${threadId}/teacher-review`,
      { method: "POST", body: JSON.stringify({ question }) },
    ),
  addTeacherReviewInformation: (requestId: string, content: string) =>
    request<TeacherReviewSubmission>(
      `/api/student/teacher-review-requests/${requestId}/additional-information`,
      { method: "POST", body: JSON.stringify({ content }) },
    ),
  teacherReviewRequests: () =>
    request<CollectionResponse<TeacherReviewRequest>>(
      "/api/student/teacher-review-requests",
    ),
  learningAnalyses: async () => {
    const response = await request<
      CollectionResponse<{
        id: string;
        status: string;
        source_grade_release_ids?: string[];
        content?: {
          summary?: string;
          strengths?: Array<{ title: string; explanation: string }>;
          weaknesses?: Array<{ title: string; explanation: string }>;
          knowledge_gaps?: Array<{ title: string; explanation: string }>;
          study_plan?: Array<{ action: string; rationale: string }>;
        };
        generated_at?: string | null;
      }>
    >("/api/student/learning-analyses");
    const finding = (items?: Array<{ title: string; explanation: string }>) =>
      items?.map((item) => `${item.title}：${item.explanation}`);
    return {
      items: collectionItems(response).map((item) => ({
        id: item.id,
        status: item.status,
        generated_at: item.generated_at,
        summary: item.content?.summary,
        strengths: finding(item.content?.strengths),
        weaknesses: finding(item.content?.weaknesses),
        knowledge_gaps: finding(item.content?.knowledge_gaps),
        recommended_actions: item.content?.study_plan?.map(
          (action) => `${action.action}：${action.rationale}`,
        ),
        source_release_count: item.source_grade_release_ids?.length ?? 0,
      })),
    };
  },
  generateLearningAnalysis: () =>
    request<StudentLearningAnalysis>("/api/student/learning-analyses", {
      method: "POST",
    }),
  resources: async () => {
    const response = await request<
      CollectionResponse<
        TeachingResource & {
          external_url?: string | null;
          file_name?: string | null;
        }
      >
    >("/api/student/resources");
    return {
      items: collectionItems(response).map((item) => ({
        ...item,
        url: item.external_url ?? item.url,
        original_name: item.file_name ?? item.original_name,
      })),
    };
  },
  resourceSignedUrl: (resourceId: string) =>
    request<{ url: string }>(
      `/api/student/resources/${resourceId}/signed-url`,
      {
        method: "POST",
      },
    ),
};

export type StudentAccountLink = {
  id: string;
  user_id: string;
  student_id: string;
  email: string;
  student_name: string;
  status: string;
  created_at: string;
  created_user?: boolean;
  temporary_password?: string | null;
};

export const studentAccountsApi = {
  link: (
    studentId: string,
    input: {
      email: string;
      display_name?: string;
      temporary_password?: string;
    },
  ) =>
    request<StudentAccountLink>(`/api/students/${studentId}/account-link`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
};

export type TeachingResourceInput = {
  title: string;
  description?: string;
  resource_type: string;
  subject?: string;
  class_id?: string;
  url?: string;
  stored_file_id?: string;
};

export const teachingResourcesApi = {
  list: async () => {
    const response = await request<
      CollectionResponse<
        TeachingResource & {
          external_url?: string | null;
          file_name?: string | null;
        }
      >
    >("/api/teaching-resources");
    return {
      items: collectionItems(response)
        .filter((item) => item.status !== "archived")
        .map((item) => ({
          ...item,
          url: item.external_url ?? item.url,
          original_name: item.file_name ?? item.original_name,
        })),
    };
  },
  uploadFile: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<StoredUpload>("/api/teaching-resources/files", {
      method: "POST",
      body,
    });
  },
  deleteUpload: (fileId: string) =>
    request<void>(`/api/teaching-resources/files/${fileId}`, {
      method: "DELETE",
    }),
  create: (input: TeachingResourceInput) =>
    request<TeachingResource>("/api/teaching-resources", {
      method: "POST",
      body: JSON.stringify({
        class_id: input.class_id,
        title: input.title,
        description: input.description,
        resource_type:
          input.resource_type === "link" ? "web" : input.resource_type,
        external_url: input.url,
        stored_file_id: input.stored_file_id,
      }),
    }),
  update: (id: string, input: Partial<TeachingResourceInput>) =>
    request<TeachingResource>(`/api/teaching-resources/${id}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  remove: (id: string) =>
    request<void>(`/api/teaching-resources/${id}`, { method: "DELETE" }),
  publish: (id: string) =>
    request<TeachingResource>(`/api/teaching-resources/${id}/publish`, {
      method: "POST",
    }),
  unpublish: (id: string) =>
    request<TeachingResource>(`/api/teaching-resources/${id}/unpublish`, {
      method: "POST",
    }),
};

export const teacherReviewRequestsApi = {
  list: () =>
    request<CollectionResponse<TeacherReviewRequest>>(
      "/api/teacher/review-requests",
    ),
  update: (
    id: string,
    input: {
      action: "uphold" | "change_score" | "needs_information" | "reject";
      teacher_response: string;
      final_score?: number;
      final_feedback?: string;
      final_error_type?: string;
    },
  ) =>
    request<TeacherReviewRequest>(`/api/teacher/review-requests/${id}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
};
