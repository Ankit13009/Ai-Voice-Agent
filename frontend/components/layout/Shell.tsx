"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth";
import { useLabels } from "@/lib/labels";
import { Button, cn } from "@/components/ui";

/**
 * Application shell: sidebar navigation, top bar, theme toggle.
 *
 * The nav is data-driven so adding a page is one array entry, and the active
 * state is derived from the pathname rather than tracked in state, which keeps
 * it correct on back/forward navigation.
 */

interface NavItem {
  href: string;
  label: string;
  /** When set, the tenant's own word replaces `label` (Patients / Clients / Members). */
  labelKey?: "customer_plural" | "booking_plural";
  icon: ReactNode;
  /** Exact match only. Used for the overview route, which is a prefix of all others. */
  exact?: boolean;
}

const icon = (path: string) => (
  <svg
    className="h-4 w-4 shrink-0"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.7"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d={path} />
  </svg>
);

const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Overview", exact: true, icon: icon("M3 12h6v9H3zM10 3h4v18h-4zM15 8h6v13h-6z") },
  { href: "/calls", label: "Calls", icon: icon("M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2 4.2 2 2 0 0 1 4 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.1a2 2 0 0 1 2.1-.5c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z") },
  { href: "/appointments", label: "Appointments", labelKey: "booking_plural" as const, icon: icon("M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z") },
  { href: "/customers", label: "Customers", labelKey: "customer_plural" as const, icon: icon("M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.9") },
  { href: "/messages", label: "WhatsApp", icon: icon("M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l2-4.9A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z") },
  { href: "/settings", label: "Settings", icon: icon("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 15a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 4.6a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0 1.2 2.9 2 2 0 1 1 0 4 1.7 1.7 0 0 0-1.5 1z") },
];

const THEME_KEY = "cr.theme";

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    const stored = window.localStorage.getItem(THEME_KEY) as "light" | "dark" | null;
    const preferred =
      stored ??
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    setTheme(preferred);
    document.documentElement.setAttribute("data-theme", preferred);
  }, []);

  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    window.localStorage.setItem(THEME_KEY, next);
  };

  return { theme, toggle };
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const { title } = useLabels();
  const { theme, toggle } = useTheme();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // Close the mobile drawer on navigation, otherwise it covers the page the
  // user just asked for.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  const isActive = (item: NavItem) =>
    item.exact ? pathname === item.href : pathname.startsWith(item.href);

  const nav = (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      {NAV_ITEMS.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          aria-current={isActive(item) ? "page" : undefined}
          className={cn(
            "flex items-center gap-2.5 rounded px-3 py-2 text-sm transition-colors",
            isActive(item)
              ? "bg-primary-soft text-primary font-medium"
              : "text-ink-muted hover:bg-surface-sunken hover:text-ink",
          )}
        >
          {item.icon}
          {item.labelKey ? title(item.labelKey) : item.label}
        </Link>
      ))}
    </nav>
  );

  return (
    <div className="min-h-screen flex">
      {/* Desktop sidebar */}
      <aside className="hidden lg:flex w-60 shrink-0 flex-col border-r border-line bg-surface">
        <div className="px-5 py-5 border-b border-line">
          <p className="text-sm font-semibold text-ink">Business Receptionist</p>
          <p className="text-xs text-ink-subtle mt-0.5">AI phone desk</p>
        </div>
        <div className="flex-1 p-3">{nav}</div>
        <SidebarFooter
          email={user?.email ?? ""}
          role={user?.role ?? ""}
          theme={theme}
          onToggleTheme={toggle}
          onLogout={logout}
        />
      </aside>

      {/* Mobile drawer */}
      {mobileNavOpen && (
        <div className="lg:hidden fixed inset-0 z-40 flex">
          <div
            className="absolute inset-0 bg-black/45"
            onClick={() => setMobileNavOpen(false)}
            aria-hidden="true"
          />
          <aside className="relative w-64 bg-surface border-r border-line flex flex-col animate-fade-in">
            <div className="px-5 py-5 border-b border-line">
              <p className="text-sm font-semibold text-ink">Business Receptionist</p>
            </div>
            <div className="flex-1 p-3">{nav}</div>
            <SidebarFooter
              email={user?.email ?? ""}
              role={user?.role ?? ""}
              theme={theme}
              onToggleTheme={toggle}
              onLogout={logout}
            />
          </aside>
        </div>
      )}

      <div className="flex-1 min-w-0 flex flex-col">
        <header className="lg:hidden flex items-center justify-between gap-3 px-4 py-3 border-b border-line bg-surface">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open navigation"
          >
            <svg
              className="h-5 w-5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              aria-hidden="true"
            >
              <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
            </svg>
          </Button>
          <p className="text-sm font-semibold text-ink">Business Receptionist</p>
          <div className="w-9" />
        </header>

        <main className="flex-1 p-4 sm:p-6 lg:p-8 max-w-[1400px] w-full mx-auto">
          {children}
        </main>
      </div>
    </div>
  );
}

function SidebarFooter({
  email,
  role,
  theme,
  onToggleTheme,
  onLogout,
}: {
  email: string;
  role: string;
  theme: string;
  onToggleTheme: () => void;
  onLogout: () => void;
}) {
  return (
    <div className="border-t border-line p-3">
      <div className="px-2 py-1.5 mb-2 min-w-0">
        <p className="text-xs font-medium text-ink truncate">{email}</p>
        <p className="text-2xs text-ink-subtle capitalize mt-0.5">{role}</p>
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleTheme}
          className="flex-1"
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
        >
          {theme === "dark" ? "Light" : "Dark"}
        </Button>
        <Button variant="ghost" size="sm" onClick={onLogout} className="flex-1">
          Sign out
        </Button>
      </div>
    </div>
  );
}
