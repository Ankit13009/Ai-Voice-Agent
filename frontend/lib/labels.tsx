"use client";

import { createContext, useContext, useMemo } from "react";
import type { ReactNode } from "react";

import { businessApi } from "@/lib/api/endpoints";
import { useApiQuery } from "@/lib/useApi";
import type { Business, BusinessLabels } from "@/types/api";

/**
 * Tenant vocabulary.
 *
 * The database and API are business-neutral (`Customer`, `StaffMember`), but a
 * clinic's staff expect to read "Patients" and a salon's expect "Clients". This
 * provider fetches the signed-in tenant's labels once and exposes them to every
 * screen, so relabelling the whole dashboard is a config change rather than a
 * fork.
 *
 * Falls back to neutral wording while loading and if the request fails: a label
 * lookup must never be the reason a page fails to render.
 */

const FALLBACK: BusinessLabels = {
  customer_singular: "Customer",
  customer_plural: "Customers",
  staff_singular: "Team member",
  staff_plural: "Team members",
  booking_singular: "appointment",
  booking_plural: "appointments",
};

interface LabelContextValue {
  labels: BusinessLabels;
  business: Business | null;
  loading: boolean;
  refresh: () => void;
  /** Capitalised for headings and buttons. */
  title: (key: keyof BusinessLabels) => string;
  /** Lower-cased for mid-sentence prose. */
  lower: (key: keyof BusinessLabels) => string;
}

const LabelContext = createContext<LabelContextValue | null>(null);

function capitalise(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export function LabelProvider({ children }: { children: ReactNode }) {
  const query = useApiQuery<Business>((signal) => businessApi.me(signal), []);

  const value = useMemo<LabelContextValue>(() => {
    const labels = { ...FALLBACK, ...(query.data?.labels ?? {}) };
    return {
      labels,
      business: query.data,
      loading: query.loading,
      refresh: query.refetch,
      title: (key) => capitalise(labels[key] ?? FALLBACK[key]),
      lower: (key) => (labels[key] ?? FALLBACK[key]).toLowerCase(),
    };
  }, [query.data, query.loading, query.refetch]);

  return <LabelContext.Provider value={value}>{children}</LabelContext.Provider>;
}

export function useLabels(): LabelContextValue {
  const context = useContext(LabelContext);
  if (!context) {
    throw new Error("useLabels must be used inside a LabelProvider.");
  }
  return context;
}
