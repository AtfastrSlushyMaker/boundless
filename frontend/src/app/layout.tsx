import type { Metadata, Viewport } from "next";
import { Providers } from "./providers";
import { SCHEME_BOOT_SCRIPT } from "@/lib/schemeBoot";
import "./globals.css";

export const metadata: Metadata = {
  title: "Boundless — Your world. Your story.",
  description: "A local-first role-playing game where your world sets the rules.",
  applicationName: "Boundless",
};

export const viewport: Viewport = {
  themeColor: [{ media: "(prefers-color-scheme: dark)", color: "#141512" }, { media: "(prefers-color-scheme: light)", color: "#f3efe7" }],
  colorScheme: "dark light",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" data-scheme="dark" suppressHydrationWarning>
    <head><script dangerouslySetInnerHTML={{ __html: SCHEME_BOOT_SCRIPT }} /></head>
    <body><Providers>{children}</Providers></body>
  </html>;
}
