"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { Shell } from "@/components/layout/Shell";
import { CalendarStatusBanner } from "@/components/integrations/CalendarStatusBanner";
import { useAuth } from "@/lib/auth";
import { tokenStore } from "@/lib/api/client";
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
    // An admin can reset a password while this session is open. The server
    // starts refusing everything at that point, so route rather than let
    // every page fill with permission errors.
    if (user?.must_change_password) {
      router.replace("/change-password");
      return;
    }
    if (!user) {
      // A token in storage means a session is being established: the user object
      // simply has not propagated through context yet. Bouncing to /login here
      // is a race that sends a freshly signed-in user straight back to the form.
      if (tokenStore.access) return;
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
      <Shell>
        {/* Above the page content on every route: a disconnected calendar stops
            the agent booking anything, and the owner has no reason to open
            settings on a day when nothing else looks wrong. */}
        <div className="mb-4 empty:mb-0">
          <CalendarStatusBanner />
        </div>
        {children}
      </Shell>
    </LabelProvider>
  );
}
