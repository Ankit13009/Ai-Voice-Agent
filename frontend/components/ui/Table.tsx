"use client";

import type { ReactNode } from "react";

import { cn } from "./utils";
import { Skeleton } from "./Feedback";

/**
 * Data table.
 *
 * Column-driven rather than children-driven: passing a `columns` array means
 * headers and cells cannot drift out of alignment, and every table in the app
 * gets the same loading, empty, and overflow behaviour without restating it.
 */

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** Tailwind width class, e.g. "w-40". Omit to size from content. */
  width?: string;
  align?: "left" | "right" | "center";
  /** Hidden below `md`. Use for secondary columns so mobile stays readable. */
  hideOnMobile?: boolean;
}

export interface TableProps<T> {
  columns: Array<Column<T>>;
  rows: T[];
  rowKey: (row: T) => string;
  loading?: boolean;
  /** Rendered in place of the body when there are no rows and loading is done. */
  empty?: ReactNode;
  onRowClick?: (row: T) => void;
  skeletonRows?: number;
}

const ALIGN = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
} as const;

export function Table<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  empty,
  onRowClick,
  skeletonRows = 6,
}: TableProps<T>) {
  const showEmpty = !loading && rows.length === 0;

  return (
    // Wide tables scroll inside this container, so the page body never scrolls
    // sideways on a narrow screen.
    <div className="scroll-x">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-line">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cn(
                  "px-5 py-2.5 text-2xs font-semibold uppercase tracking-wide text-ink-subtle whitespace-nowrap",
                  ALIGN[column.align ?? "left"],
                  column.width,
                  column.hideOnMobile && "hidden md:table-cell",
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {loading &&
            Array.from({ length: skeletonRows }).map((_, index) => (
              <tr key={`skeleton-${index}`} className="border-b border-line last:border-0">
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={cn(
                      "px-5 py-3",
                      column.hideOnMobile && "hidden md:table-cell",
                    )}
                  >
                    <Skeleton className="h-4 w-full max-w-[10rem]" />
                  </td>
                ))}
              </tr>
            ))}

          {showEmpty && (
            <tr>
              <td colSpan={columns.length} className="px-5 py-12">
                {empty ?? (
                  <p className="text-center text-sm text-ink-subtle">Nothing to show yet.</p>
                )}
              </td>
            </tr>
          )}

          {!loading &&
            rows.map((row) => (
              <tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                // A clickable row needs to be reachable and activatable by
                // keyboard, not just by mouse.
                tabIndex={onRowClick ? 0 : undefined}
                role={onRowClick ? "button" : undefined}
                onKeyDown={
                  onRowClick
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onRowClick(row);
                        }
                      }
                    : undefined
                }
                className={cn(
                  "border-b border-line last:border-0 transition-colors",
                  onRowClick &&
                    "cursor-pointer hover:bg-surface-sunken focus-visible:bg-surface-sunken focus-visible:outline-none",
                )}
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={cn(
                      "px-5 py-3 text-ink align-middle",
                      ALIGN[column.align ?? "left"],
                      column.hideOnMobile && "hidden md:table-cell",
                    )}
                  >
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}

/** Primary text plus a quieter second line, the common two-line table cell. */
export function CellStack({
  primary,
  secondary,
}: {
  primary: ReactNode;
  secondary?: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <div className="text-sm text-ink truncate">{primary}</div>
      {secondary && (
        <div className="text-xs text-ink-subtle truncate mt-0.5">{secondary}</div>
      )}
    </div>
  );
}
