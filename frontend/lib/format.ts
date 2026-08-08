/**
 * Display formatting.
 *
 * Centralised so a duration reads the same on the call log as on the dashboard.
 *
 * A note on timezones: the API already sends `starts_at_local` pre-rendered in
 * the *business's* timezone. Prefer that field over reformatting `starts_at` here,
 * because the browser's timezone is the staff member's, which is not
 * necessarily the business's.
 */

export function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return "0s";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

/** Absolute date and time in the viewer's locale. For timestamps, not appointments. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

/** "2 hours ago", "in 3 days". Used where recency matters more than the exact time. */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";

  const diffMs = date.getTime() - Date.now();
  const diffMinutes = Math.round(diffMs / 60000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

  const thresholds: Array<[number, Intl.RelativeTimeFormatUnit]> = [
    [60, "minute"],
    [24 * 60, "hour"],
    [30 * 24 * 60, "day"],
  ];

  if (Math.abs(diffMinutes) < 1) return "just now";
  for (const [limit, unit] of thresholds) {
    if (Math.abs(diffMinutes) < limit) {
      const divisor = unit === "minute" ? 1 : unit === "hour" ? 60 : 24 * 60;
      return formatter.format(Math.round(diffMinutes / divisor), unit);
    }
  }
  return formatDate(iso);
}

/**
 * Group Indian numbers for readability: +91 98765 43210.
 * Falls back to the raw string for anything that is not a 10-digit +91 number,
 * rather than mangling an unexpected format.
 */
export function formatPhone(phone: string): string {
  if (!phone) return "—";
  const digits = phone.replace(/\D/g, "");
  if (digits.length === 12 && digits.startsWith("91")) {
    return `+91 ${digits.slice(2, 7)} ${digits.slice(7)}`;
  }
  if (digits.length === 10) return `${digits.slice(0, 5)} ${digits.slice(5)}`;
  return phone;
}

/** Paise to rupees, e.g. 12345 -> "₹123.45". */
export function formatPaise(paise: number): string {
  return `₹${(paise / 100).toFixed(2)}`;
}

const WEEKDAYS = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** ISO weekday numbers to "Mon, Tue, Wed". */
export function formatWorkingDays(days: number[]): string {
  if (!days?.length) return "—";
  return days
    .slice()
    .sort((a, b) => a - b)
    .map((day) => WEEKDAYS[day] ?? day)
    .join(", ");
}

/** "09:00:00" -> "9:00 AM". */
export function formatTime(value: string): string {
  if (!value) return "—";
  const [hourPart, minutePart] = value.split(":");
  const hour = Number(hourPart);
  if (Number.isNaN(hour)) return value;
  const suffix = hour >= 12 ? "PM" : "AM";
  const display = hour % 12 === 0 ? 12 : hour % 12;
  return `${display}:${minutePart ?? "00"} ${suffix}`;
}

/**
 * Datetime-local input value ("2026-08-12T15:00") to an offset-bearing ISO
 * string. The API rejects naive datetimes, and the browser's own offset is the
 * only correct interpretation of what the user typed into a local input.
 */
export function localInputToIso(value: string): string {
  if (!value) return "";
  return new Date(value).toISOString();
}

/** The inverse, for pre-filling a datetime-local input from an API value. */
export function isoToLocalInput(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const offsetMs = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}
