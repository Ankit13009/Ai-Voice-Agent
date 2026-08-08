"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import type { Paginated } from "@/types/api";

/**
 * Data fetching hooks.
 *
 * Small on purpose. The app has no server-state library, and these two hooks
 * cover every screen: fetch on mount with a refetch handle, and fetch a
 * paginated list with filters.
 *
 * Both abort the in-flight request when their inputs change or the component
 * unmounts, which is what prevents a slow first response from overwriting a
 * newer one and putting stale rows on screen.
 */

interface QueryState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
}

export function useApiQuery<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[] = [],
): QueryState<T> & { refetch: () => void } {
  const [state, setState] = useState<QueryState<T>>({
    data: null,
    loading: true,
    error: null,
  });
  const [tick, setTick] = useState(0);

  // Held in a ref so changing the fetcher identity every render does not
  // re-trigger the effect; `deps` is the explicit invalidation signal.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    setState((current) => ({ ...current, loading: true, error: null }));

    fetcherRef
      .current(controller.signal)
      .then((data) => {
        if (active) setState({ data, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (!active) return;
        if (error instanceof Error && error.name === "AbortError") return;
        setState({
          data: null,
          loading: false,
          error:
            error instanceof ApiError
              ? error
              : new ApiError("INTERNAL_ERROR", "Could not load this data.", 0),
        });
      });

    return () => {
      active = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const refetch = useCallback(() => setTick((value) => value + 1), []);

  return { ...state, refetch };
}

/**
 * Paginated list with page state built in.
 *
 * `filterKey` is a stable string describing the current filters. When it
 * changes the list resets to page 1, because staying on page 4 of a result set
 * that now has two pages shows an empty table and looks like a bug.
 */
export function useApiList<T>(
  fetcher: (page: number, signal: AbortSignal) => Promise<Paginated<T>>,
  filterKey: string,
) {
  const [page, setPage] = useState(1);

  useEffect(() => {
    setPage(1);
  }, [filterKey]);

  const query = useApiQuery<Paginated<T>>(
    (signal) => fetcher(page, signal),
    [page, filterKey],
  );

  return {
    items: query.data?.items ?? [],
    meta: query.data?.meta ?? null,
    loading: query.loading,
    error: query.error,
    page,
    setPage,
    refetch: query.refetch,
  };
}

/**
 * A mutation with its own pending flag, so a button can show a spinner and
 * block double submission without each page tracking that itself.
 */
export function useMutation<TArgs extends unknown[], TResult>(
  action: (...args: TArgs) => Promise<TResult>,
) {
  const [pending, setPending] = useState(false);

  const run = useCallback(
    async (...args: TArgs): Promise<TResult> => {
      setPending(true);
      try {
        return await action(...args);
      } finally {
        setPending(false);
      }
    },
    [action],
  );

  return { run, pending };
}
