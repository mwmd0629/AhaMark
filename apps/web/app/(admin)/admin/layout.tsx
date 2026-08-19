import { AdminShell } from "@/components/admin-shell";
import { AuthGate } from "@/components/auth-gate";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthGate audience="admin">
      <AdminShell>{children}</AdminShell>
    </AuthGate>
  );
}
