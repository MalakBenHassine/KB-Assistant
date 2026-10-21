import type { Metadata } from "next";

import { DocumentsView } from "@/components/documents/documents-view";

export const metadata: Metadata = { title: "Documents · KB Assistant" };

export default function DocumentsPage() {
  return <DocumentsView />;
}
