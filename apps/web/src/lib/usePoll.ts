"use client";
import { useEffect, useState } from "react";
import { api, ApiError } from "./api";

/** Poll an API path. The payload is keyed by its path, so a changed path never shows the previous
 *  path's data (that would be a different shape and could crash a page during the switch). */
export function usePoll<T>(path: string, everyMs = 5000): { data: T | null; error: string | null } {
  const [state, setState] = useState<{ path: string; data: T } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const d = await api<T>(path);
        if (alive) {
          setState({ path, data: d });
          setError(null);
        }
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 401) {
          window.location.href = "/login";
          return;
        }
        setError(e instanceof ApiError ? `Request failed (${e.status})` : "Network error");
      }
    };
    void tick();
    const id = setInterval(tick, everyMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [path, everyMs]);
  return { data: state && state.path === path ? state.data : null, error };
}
