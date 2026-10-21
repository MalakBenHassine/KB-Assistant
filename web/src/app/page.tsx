import { redirect } from "next/navigation";

// `proxy.ts` has already sent anonymous visitors to /login.
export default function Home() {
  redirect("/chat");
}
