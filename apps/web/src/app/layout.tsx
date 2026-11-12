import type { ReactNode } from "react";
import "./globals.css";

export const metadata = { title: "ASTA", description: "Spot trading research (no live trading)" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
