"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

import { ApiError } from "@/lib/api/client";
import { cn } from "./utils";

/**
 * Toast notifications.
 *
 * The `showApiError` helper is the reason this exists as a provider rather than
 * a component: every mutation in the app ends in either a success message from
 * the API envelope or an `ApiError`, and both should surface identically. It
 * also appends the `request_id` on server faults, which is what turns a support
 * message into something traceable in the logs.
 */

export type ToastTone = "success" | "error" | "info";

interface Toast {
  id: number;
  tone: ToastTone;
  message: string;
  detail?: string;
}

interface ToastContextValue {
  show: (message: string, tone?: ToastTone, detail?: string) => void;
  showSuccess: (message: string | null | undefined, fallback?: string) => void;
  showApiError: (error: unknown, fallback?: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const AUTO_DISMISS_MS = 5000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const show = useCallback(
    (message: string, tone: ToastTone = "info", detail?: string) => {
      const id = nextId.current++;
      setToasts((current) => [...current, { id, tone, message, detail }]);
      window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    },
    [dismiss],
  );

  const showSuccess = useCallback(
    (message: string | null | undefined, fallback = "Done.") => {
      show(message || fallback, "success");
    },
    [show],
  );

  const showApiError = useCallback(
    (error: unknown, fallback = "Something went wrong.") => {
      if (error instanceof ApiError) {
        // Field-level problems belong inline on the form, not in a toast, so
        // only the summary line is shown here.
        const detail =
          error.status >= 500 && error.requestId
            ? `Reference: ${error.requestId}`
            : undefined;
        show(error.message || fallback, "error", detail);
        return;
      }
      if (error instanceof Error && error.name === "AbortError") return;
      show(fallback, "error");
    },
    [show],
  );

  const value = useMemo(
    () => ({ show, showSuccess, showApiError }),
    [show, showSuccess, showApiError],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* aria-live so new toasts are announced without stealing focus. */}
      <div
        className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2 w-full max-w-sm pointer-events-none"
        aria-live="polite"
        aria-atomic="false"
      >
        {toasts.map((toast) => (
          <ToastItem key={toast.id} toast={toast} onDismiss={() => dismiss(toast.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

const TONE_STYLES: Record<ToastTone, string> = {
  success: "border-success/30 bg-success-soft text-success",
  error: "border-danger/30 bg-danger-soft text-danger",
  info: "border-line bg-surface-raised text-ink",
};

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  return (
    <div
      className={cn(
        "pointer-events-auto flex items-start gap-3 rounded-lg border px-4 py-3 shadow-md animate-fade-in",
        TONE_STYLES[toast.tone],
      )}
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{toast.message}</p>
        {toast.detail && (
          <p className="mt-0.5 text-xs opacity-75 font-mono">{toast.detail}</p>
        )}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss notification"
        className="shrink-0 opacity-60 hover:opacity-100 transition-opacity"
      >
        <svg
          className="h-4 w-4"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.75"
          aria-hidden="true"
        >
          <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used inside a ToastProvider.");
  }
  return context;
}
