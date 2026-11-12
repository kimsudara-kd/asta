"use client";
import { useSearchParams } from "next/navigation";

/** Read a query-string value. Reacts to client-side navigation (e.g. clicking a run in a list).
 *  Pages using it must render inside a <Suspense> boundary. */
export function useQueryParam(name: string): string | null {
  return useSearchParams().get(name);
}
