import { AssignmentWizard } from "@/components/assignment-wizard";

export default async function EditAssignmentPage({
  params,
  searchParams,
}: {
  params: Promise<{ assignmentId: string }>;
  searchParams: Promise<{ step?: string }>;
}) {
  const requestedStep = Number((await searchParams).step);
  const initialStep =
    Number.isInteger(requestedStep) && requestedStep >= 1 && requestedStep <= 3
      ? requestedStep
      : undefined;
  return (
    <AssignmentWizard
      assignmentId={(await params).assignmentId}
      initialStep={initialStep}
    />
  );
}
