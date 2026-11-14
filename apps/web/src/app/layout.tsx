import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import PwaRegister from "../components/PwaRegister";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "ASTA", template: "%s · ASTA" },
  description: "Spot trading research and paper trading (simulation only, no live trading)",
  applicationName: "ASTA",
  appleWebApp: { capable: true, title: "ASTA", statusBarStyle: "black-translucent" },
  formatDetection: { telephone: false },
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: [{ url: "/apple-icon.png", sizes: "180x180", type: "image/png" }],
  },
  other: { "apple-mobile-web-app-capable": "yes" }, // older iOS versions only read this form
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover", // use the whole screen; CSS keeps content inside the safe areas (notch, home bar)
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#090d13" },
    { media: "(prefers-color-scheme: light)", color: "#f3f5f9" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <PwaRegister />
      </body>
    </html>
  );
}
