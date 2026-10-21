import type { Metadata } from "next";
import { connection } from "next/server";

import "./globals.css";

export const metadata: Metadata = {
  title: "KB Assistant",
  description:
    "Answers from your company's documents, with citations, limited to what you are allowed to read.",
};

// Every page is rendered per request: the CSP nonce is per response, and a
// prerendered page would carry none (ADR-0020). System fonts: nothing fetched
// from a third party at build or run time.
export default async function RootLayout({ children }: LayoutProps<"/">) {
  await connection();
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
