import type { MetadataRoute } from "next";

// Web app manifest (served at /manifest.webmanifest). Installable on Android/desktop Chrome/Edge, and the
// basis of "Add to Home Screen" on iOS (which also reads the apple-* tags in layout.tsx).
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "ASTA - Spot research platform",
    short_name: "ASTA",
    description:
      "Spot crypto research and paper-trading console. Simulation only: no orders, no credentials, no real funds.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    display_override: ["standalone", "minimal-ui"],
    orientation: "any",
    background_color: "#090d13",
    theme_color: "#090d13",
    categories: ["finance", "productivity"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [
      { name: "Markets", url: "/markets", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
      { name: "Paper trading", url: "/paper", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
      { name: "Risk", url: "/risk", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
    ],
  };
}
