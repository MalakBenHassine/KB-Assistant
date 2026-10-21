import type { Metadata } from "next";

import { ChatView } from "@/components/chat/chat-view";

export const metadata: Metadata = { title: "Conversation · KB Assistant" };

export default async function ConversationPage({ params }: PageProps<"/chat/[conversationId]">) {
  const { conversationId } = await params;
  // Keyed: another conversation is a fresh view, not a reused one with stale state.
  return <ChatView key={conversationId} conversationId={conversationId} />;
}
