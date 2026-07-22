import { AssignmentWizard } from "@/components/assignment-wizard";

export default async function EditAssignmentPage({
  params,
}: {
  params: Promise<{ assignmentId: string }>;
}) {
  return <AssignmentWizard assignmentId={(await params).assignmentId} />;
}
