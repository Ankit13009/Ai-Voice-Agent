"use client";

import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn, type VariantMap } from "./utils";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "link";
export type ButtonSize = "sm" | "md" | "lg";

/**
 * Variants are a closed set on purpose.
 *
 * A `className` escape hatch exists for layout (width, margin), but the visual
 * treatment comes from here. That is what stops the twelfth button in the app
 * from being a slightly different shade of teal.
 */
const VARIANTS: VariantMap<ButtonVariant> = {
  primary:
    "bg-primary text-ink-inverse shadow-xs hover:bg-primary-hover active:bg-primary-hover disabled:hover:bg-primary",
  secondary:
    "bg-surface text-ink border border-line shadow-xs hover:bg-surface-sunken hover:border-line-strong disabled:hover:bg-surface",
  ghost: "text-ink-muted hover:bg-surface-sunken hover:text-ink disabled:hover:bg-transparent",
  danger:
    "bg-danger text-white shadow-xs hover:bg-danger-hover active:bg-danger-hover disabled:hover:bg-danger",
  link: "text-primary underline underline-offset-4 hover:text-primary-hover disabled:no-underline",
};

const SIZES: VariantMap<ButtonSize> = {
  sm: "h-8 px-3 text-xs gap-1.5 rounded-sm",
  md: "h-9 px-4 text-sm gap-2 rounded",
  lg: "h-11 px-5 text-base gap-2 rounded-lg",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner and blocks clicks. Keeps the label so width stays stable. */
  loading?: boolean;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
  fullWidth?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    leadingIcon,
    trailingIcon,
    fullWidth = false,
    className,
    children,
    disabled,
    type = "button",
    ...props
  },
  ref,
) {
  const isDisabled = disabled || loading;

  return (
    <button
      ref={ref}
      type={type}
      disabled={isDisabled}
      // Communicates the busy state to assistive tech, which cannot see the spinner.
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex items-center justify-center font-medium whitespace-nowrap",
        "transition-colors duration-150",
        "disabled:opacity-55 disabled:cursor-not-allowed",
        VARIANTS[variant],
        SIZES[size],
        fullWidth && "w-full",
        className,
      )}
      {...props}
    >
      {loading ? (
        <Spinner className={size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4"} />
      ) : (
        leadingIcon
      )}
      {children}
      {!loading && trailingIcon}
    </button>
  );
});

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cn("animate-spin", className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="3"
      />
      <path
        className="opacity-90"
        fill="currentColor"
        d="M4 12a8 8 0 0 1 8-8v3a5 5 0 0 0-5 5H4z"
      />
    </svg>
  );
}
