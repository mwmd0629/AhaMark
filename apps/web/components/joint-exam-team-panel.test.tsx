import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { JointExamTeamPanel } from "./joint-exam-team-panel";

const api = vi.hoisted(() => ({
  jointTeam: vi.fn(),
  invite: vi.fn(),
  authorize: vi.fn(),
  remove: vi.fn(),
  classes: vi.fn(),
}));

vi.mock("@/lib/api", async (load) => {
  const actual = await load<typeof import("@/lib/api")>();
  return {
    ...actual,
    assignmentsApi: {
      ...actual.assignmentsApi,
      jointTeam: api.jointTeam,
      inviteJointCollaborator: api.invite,
      authorizeJointClasses: api.authorize,
      removeJointClass: api.remove,
    },
    classesApi: { ...actual.classesApi, list: api.classes },
  };
});

const team = {
  assignment_id: "joint-1",
  title: "大学物理联考",
  status: "draft" as const,
  is_owner: true,
  owner: {
    id: "owner-1",
    display_name: "主责老师",
    email: "owner@example.com",
  },
  collaborators: [],
  classes: [
    {
      id: "class-1",
      name: "主责班",
      owner_id: "owner-1",
      owner_name: "主责老师",
      authorized_by: "owner-1",
      authorized: true,
      mine: true,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  api.jointTeam.mockResolvedValue(team);
  api.invite.mockResolvedValue({
    ...team,
    collaborators: [
      {
        id: "teacher-2",
        display_name: "协作老师",
        email: "teacher2@example.com",
        role: "grader",
      },
    ],
  });
  api.authorize.mockResolvedValue({
    ...team,
    classes: [
      ...team.classes,
      {
        id: "class-2",
        name: "我的二班",
        owner_id: "owner-1",
        owner_name: "主责老师",
        authorized_by: "owner-1",
        authorized: true,
        mine: true,
      },
    ],
  });
  api.classes.mockResolvedValue({
    items: [
      {
        id: "class-2",
        name: "我的二班",
        status: "active",
        student_count: 20,
        active_student_count: 20,
        group_count: 0,
      },
    ],
  });
});

afterEach(cleanup);

it("邀请教师并且只能从当前账号班级中授权加入联考", async () => {
  render(<JointExamTeamPanel assignmentId="joint-1" />);

  fireEvent.change(
    await screen.findByRole("textbox", { name: /邀请教师邮箱/ }),
    {
      target: { value: "teacher2@example.com" },
    },
  );
  fireEvent.click(screen.getByRole("button", { name: "邀请" }));
  await waitFor(() =>
    expect(api.invite).toHaveBeenCalledWith("joint-1", "teacher2@example.com"),
  );

  fireEvent.change(screen.getByRole("combobox", { name: "授权我的班级加入" }), {
    target: { value: "class-2" },
  });
  fireEvent.click(screen.getByRole("button", { name: "加入联考" }));
  await waitFor(() =>
    expect(api.authorize).toHaveBeenCalledWith("joint-1", ["class-2"]),
  );
});
