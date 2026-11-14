"use client";
import { useEffect } from "react";

/** Registers the service worker (production builds only; never in `next dev`). No UI: the browser's own
 *  install prompt / "Add to Home Screen" is used, so no extra controls appear on any page. */
export default function PwaRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    const register = () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
        /* a failed registration only means no offline page; the app itself is unaffected */
      });
    };
    if (document.readyState === "complete") register();
    else window.addEventListener("load", register, { once: true });
    return () => window.removeEventListener("load", register);
  }, []);
  return null;
}
