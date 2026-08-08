"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { Shell } from "@/components/layout/Shell";
import { useAuth } from "@/lib/auth";
import { LabelProvider } from "@/lib/labels";
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
    if (loading) return;
    if (!user) {
      router.replace("/login");
    } else if (user.role === "superadmin") {
      // A superadmin has no tenant, so these pages would 403 on every request.
      router.replace("/admin");
    }
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="p-8 max-w-[1400px] mx-auto">
        <PageLoading />
      </div>
    );
  }

  // Render nothing during the redirect rather than flashing the shell.
  if (!user || user.role === "superadmin") return null;

  return (
    <LabelProvider>
      <Shell>{children}</Shell>
    </LabelProvider>
  );
}
