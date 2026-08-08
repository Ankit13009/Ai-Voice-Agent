"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { Shell } from "@/components/layout/Shell";
import { useAuth } from "@/lib/auth";
import { PageLoading } from "@/components/ui";

/**
 * Guards every dashboard route.
 *
 * This is convenience, not security. The tokens are checked server-side on
 * every request, so a user who bypasses this redirect sees empty pages and
 * 401s rather than anyone else's data.
 */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="p-8 max-w-[1400px] mx-auto">
        <PageLoading />
      </div>
    );
  }

  // Render nothing during the redirect rather than flashing the shell.
  if (!user) return null;

  return <Shell>{children}</Shell>;
}
