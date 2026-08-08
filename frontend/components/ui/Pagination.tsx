"use client";

import type { PaginationMeta } from "@/types/api";
import { Button } from "./Button";

/**
 * Pagination bar driven directly by the API's `meta` block.
 *
 * Taking `PaginationMeta` rather than loose numbers means the control cannot
 * disagree with the server about how many pages exist.
 */
export function Pagination({
  meta,
  onPageChange,
  disabled = false,
}: {
  meta: PaginationMeta;
  onPageChange: (page: number) => void;
  disabled?: boolean;
}) {
  // Nothing to navigate: hide rather than showing a dead control.
  if (meta.total_pages <= 1) return null;

  const firstItem = (meta.page - 1) * meta.page_size + 1;
  const lastItem = Math.min(meta.page * meta.page_size, meta.total);

  return (
    <div className="flex items-center justify-between gap-4 px-5 py-3 border-t border-line">
      <p className="text-xs text-ink-subtle tnum">
        {firstItem}-{lastItem} of {meta.total}
      </p>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          disabled={disabled || !meta.has_prev}
          onClick={() => onPageChange(meta.page - 1)}
        >
          Previous
        </Button>
        <span className="text-xs text-ink-subtle tnum px-1">
          {meta.page} / {meta.total_pages}
        </span>
        <Button
          size="sm"
          variant="secondary"
          disabled={disabled || !meta.has_next}
          onClick={() => onPageChange(meta.page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
