/**
 * Class name joiner.
 *
 * Deliberately dependency-free: the app does not need `clsx` + `tailwind-merge`
 * for this, and the components below are written so variant classes never
 * conflict with each other in the first place.
 */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/**
 * Type helper for the variant maps each component defines.
 * Keeping variants as plain objects makes the full set greppable, and makes an
 * invalid variant a compile error rather than a silently missing style.
 */
export type VariantMap<T extends string> = Record<T, string>;
