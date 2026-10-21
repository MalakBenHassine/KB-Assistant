"use client";

import {
  createContext,
  use,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { getJson } from "@/lib/client";
import type { Conversation, Department, Me, Page } from "@/lib/types";

type Session = {
  me: Me | null;
  /** Departments the person can search: every department for a tenant admin. */
  readable: Department[];
  conversations: Conversation[];
  reloadConversations: () => Promise<void>;
};

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [all, setAll] = useState<Department[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);

  const reloadConversations = useCallback(async () => {
    setConversations((await getJson<Page<Conversation>>("/api/v1/conversations")).items);
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      const profile = await getJson<Me>("/api/v1/me");
      const departments =
        profile.role === "tenant_admin"
          ? (await getJson<Page<Department>>("/api/v1/departments")).items
          : profile.departments;
      const listed = (await getJson<Page<Conversation>>("/api/v1/conversations")).items;
      if (!active) return;
      setMe(profile);
      setAll(departments);
      setConversations(listed);
    })().catch(() => undefined); // a 401 redirects to /login in `request`
    return () => {
      active = false;
    };
  }, []);

  const value = useMemo(
    () => ({ me, readable: all, conversations, reloadConversations }),
    [me, all, conversations, reloadConversations],
  );
  return <SessionContext value={value}>{children}</SessionContext>;
}

export function useSession(): Session {
  const session = use(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
