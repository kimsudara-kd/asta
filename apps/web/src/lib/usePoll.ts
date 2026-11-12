"use client";
import { useEffect, useState } from "react";
import { api, ApiError } from "./api";

export function usePoll<T>(path: string, everyMs = 5000): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const d = await api<T>(path);
        if (alive) {
          setData(d);
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
  return { data, error };
}
