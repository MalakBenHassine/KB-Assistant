import type { Metadata } from "next";

import { AdminView } from "@/components/admin/admin-view";

export const metadata: Metadata = { title: "Administration · KB Assistant" };

export default function AdminPage() {
  return <AdminView />;
}
