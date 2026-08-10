"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth";
import { tokenStore } from "@/lib/api/client";
import { Button, PageLoading } from "@/components/ui";

/**
 * Operator area, for superadmins only.
 *
 * Deliberately outside the `(dashboard)` route group. Those pages call
 * `/businesses/me`, which a superadmin cannot use: they have no `business_id`,
 * so every tenant-scoped endpoint correctly refuses them. Rather than weaken
 * that rule, the operator gets its own shell with no tenant context and no
 * label provider.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { user, loading, logout } = useAuth();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      // A token in storage means a session is being established: the user object
      // simply has not propagated through context yet. Bouncing to /login here
      // is a race that sends a freshly signed-in user straight back to the form.
      if (tokenStore.access) return;
      router.replace("/login");
    } else if (user.role !== "superadmin") {
      // A tenant user who wanders here goes to their own dashboard. The API
      // would reject them anyway; this just avoids a wall of 403s.
      router.replace("/");
    }
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="p-8 max-w-[1400px] mx-auto">
        <PageLoading />
      </div>
    );
  }

  if (!user || user.role !== "superadmin") return null;

  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-line bg-surface">
        <div className="max-w-[1200px] mx-auto px-6 py-3 flex items-center justify-between gap-4">
          <div className="flex items-center gap-6 min-w-0">
            <Link href="/admin" className="text-sm font-semibold text-ink whitespace-nowrap">
              Receptionist Platform
            </Link>
            <nav className="flex items-center gap-1" aria-label="Admin">
              <Link
                href="/admin"
                className="rounded px-3 py-1.5 text-sm text-ink-muted hover:bg-surface-sunken hover:text-ink transition-colors"
              >
                Clients
              </Link>
              <Link
                href="/admin/onboard"
                className="rounded px-3 py-1.5 text-sm text-ink-muted hover:bg-surface-sunken hover:text-ink transition-colors"
              >
                Add client
              </Link>
            </nav>
          </div>
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-xs text-ink-subtle truncate hidden sm:block">
              {user.email}
            </span>
            <Button variant="ghost" size="sm" onClick={logout}>
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <main className="max-w-[1200px] mx-auto px-6 py-8">{children}</main>
    </div>
  );
}
