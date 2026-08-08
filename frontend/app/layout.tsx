import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AuthProvider } from "@/lib/auth";
import { ToastProvider } from "@/components/ui";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "Clinic Receptionist",
  description: "AI phone receptionist for clinics: calls, appointments, reminders.",
};

/**
 * The theme attribute is set by an inline script before React hydrates.
 *
 * Without this the page paints in light mode and then flips to dark on mount,
 * which is a visible flash on every navigation for dark-mode users.
 */
const THEME_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem('cr.theme');
    var dark = stored ? stored === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  } catch (e) {}
})();
`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <AuthProvider>
          <ToastProvider>{children}</ToastProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
