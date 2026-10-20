import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KB Assistant",
  description:
    "Answers from your company's documents, with citations, limited to what you are allowed to read.",
};

// System fonts: no build-time download from a third party, nothing to leak at runtime.
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
