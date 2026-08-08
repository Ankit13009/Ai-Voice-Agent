"use client";

import { createContext, forwardRef, useContext, useId } from "react";
import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

import { cn } from "./utils";

/**
 * Form controls.
 *
 * `Field` owns the label, hint, and error, and passes the generated id and
 * error state down through context. Controls therefore get `aria-invalid` and
 * `aria-describedby` wired automatically, which is the part hand-rolled forms
 * reliably forget and screen-reader users reliably need.
 */

interface FieldContextValue {
  id: string;
  describedBy?: string;
  invalid: boolean;
}

const FieldContext = createContext<FieldContextValue | null>(null);

function useFieldContext() {
  return useContext(FieldContext);
}

export interface FieldProps {
  label?: string;
  /** Helper text shown when there is no error. */
  hint?: string;
  /** Error text. Its presence is what marks the control invalid. */
  error?: string;
  required?: boolean;
  className?: string;
  children: ReactNode;
}

export function Field({
  label,
  hint,
  error,
  required,
  className,
  children,
}: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = error ? errorId : hint ? hintId : undefined;

  return (
    <FieldContext.Provider value={{ id, describedBy, invalid: Boolean(error) }}>
      <div className={cn("flex flex-col gap-1.5", className)}>
        {label && (
          <label htmlFor={id} className="text-sm font-medium text-ink">
            {label}
            {required && (
              <span className="text-danger ml-0.5" aria-hidden="true">
                *
              </span>
            )}
          </label>
        )}
        {children}
        {/* Error replaces the hint rather than stacking, so the layout does not
            jump and the user reads one instruction, not two. */}
        {error ? (
          <p id={errorId} role="alert" className="text-xs text-danger">
            {error}
          </p>
        ) : hint ? (
          <p id={hintId} className="text-xs text-ink-subtle">
            {hint}
          </p>
        ) : null}
      </div>
    </FieldContext.Provider>
  );
}

const CONTROL_BASE = cn(
  "w-full rounded bg-surface text-ink placeholder:text-ink-subtle",
  "border border-line shadow-xs",
  "transition-colors duration-150",
  "hover:border-line-strong",
  "focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/25",
  "disabled:bg-surface-sunken disabled:text-ink-subtle disabled:cursor-not-allowed",
  "aria-[invalid=true]:border-danger aria-[invalid=true]:focus:ring-danger/25",
);

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** Rendered inside the control's left edge, e.g. a search icon. */
  leadingIcon?: ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, leadingIcon, ...props },
  ref,
) {
  const field = useFieldContext();
  const control = (
    <input
      ref={ref}
      id={field?.id ?? props.id}
      aria-invalid={field?.invalid || undefined}
      aria-describedby={field?.describedBy}
      className={cn(
        CONTROL_BASE,
        "h-9 px-3 text-sm",
        // Boolean(): ReactNode can be 0, which would otherwise leak into cn().
        Boolean(leadingIcon) && "pl-9",
        className,
      )}
      {...props}
    />
  );

  if (!leadingIcon) return control;
  return (
    <div className="relative">
      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-subtle pointer-events-none">
        {leadingIcon}
      </span>
      {control}
    </div>
  );
});

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, rows = 4, ...props }, ref) {
  const field = useFieldContext();
  return (
    <textarea
      ref={ref}
      id={field?.id ?? props.id}
      rows={rows}
      aria-invalid={field?.invalid || undefined}
      aria-describedby={field?.describedBy}
      className={cn(CONTROL_BASE, "px-3 py-2 text-sm resize-y", className)}
      {...props}
    />
  );
});

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  options: Array<{ value: string; label: string }>;
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, options, placeholder, ...props },
  ref,
) {
  const field = useFieldContext();
  return (
    <div className="relative">
      <select
        ref={ref}
        id={field?.id ?? props.id}
        aria-invalid={field?.invalid || undefined}
        aria-describedby={field?.describedBy}
        className={cn(
          CONTROL_BASE,
          "h-9 pl-3 pr-9 text-sm appearance-none cursor-pointer",
          className,
        )}
        {...props}
      >
        {placeholder && <option value="">{placeholder}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <svg
        className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-subtle pointer-events-none"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        aria-hidden="true"
      >
        <path d="M6 8l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
});

export interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
}

export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled,
}: SwitchProps) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4 py-2">
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm font-medium text-ink cursor-pointer">
          {label}
        </label>
        {description && (
          <p className="text-xs text-ink-subtle mt-0.5">{description}</p>
        )}
      </div>
      {/* A real checkbox under a styled track: keyboard, form semantics, and
          screen-reader announcements all work without reimplementing them. */}
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative shrink-0 h-6 w-11 rounded-full transition-colors duration-200",
          "focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          checked ? "bg-primary" : "bg-line-strong",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-200",
            checked ? "translate-x-[22px]" : "translate-x-0.5",
          )}
        />
      </button>
    </div>
  );
}
