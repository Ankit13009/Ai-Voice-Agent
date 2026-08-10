"use client";

import { cn } from "@/components/ui/utils";

export type SettingsSectionId =
  | "agent"
  | "schedule"
  | "staff"
  | "integrations"
  | "whatsapp"
  | "users"
  | "data";

export interface SettingsSection {
  id: SettingsSectionId;
  label: string;
  hint: string;
}

/**
 * Section switcher for the settings page.
 *
 * Everything used to sit on one scrolling page, which put seven unrelated
 * cards between the owner and whatever they came to change. Splitting them
 * keeps each screen to one decision, and the section lives in the URL so a
 * support conversation can say "open /settings?section=integrations" instead
 * of "scroll down to about halfway".
 *
 * A vertical list on desktop, a horizontal scroller on mobile: a sidebar
 * beside a form is unusable at phone width.
 */
export function SettingsNav({
  sections,
  active,
  onSelect,
}: {
  sections: SettingsSection[];
  active: SettingsSectionId;
  onSelect: (id: SettingsSectionId) => void;
}) {
  return (
    <nav
      aria-label="Settings sections"
      className={cn(
        "flex gap-1 overflow-x-auto pb-2 -mx-1 px-1",
        "lg:flex-col lg:overflow-visible lg:pb-0 lg:mx-0 lg:px-0 lg:sticky lg:top-6",
      )}
    >
      {sections.map((section) => {
        const isActive = section.id === active;
        return (
          <button
            key={section.id}
            type="button"
            onClick={() => onSelect(section.id)}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "shrink-0 text-left rounded-lg px-3 py-2 transition-colors",
              "focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:outline-none",
              isActive
                ? "bg-primary-soft text-primary"
                : "text-ink-muted hover:bg-surface-sunken hover:text-ink",
            )}
          >
            <span className="block text-sm font-medium whitespace-nowrap">{section.label}</span>
            {/* The hint explains what lives inside, so the owner does not have to
                open three sections to find the one they wanted. Hidden on mobile,
                where the tabs are horizontal and there is no room for it. */}
            <span className="hidden lg:block text-xs opacity-70 mt-0.5">{section.hint}</span>
          </button>
        );
      })}
    </nav>
  );
}
