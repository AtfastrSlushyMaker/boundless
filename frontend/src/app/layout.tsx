import type { Metadata, Viewport } from "next";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "Boundless — Your world. Your story.",
  description: "A local-first role-playing game where your world sets the rules.",
  applicationName: "Boundless",
};

export const viewport: Viewport = { themeColor: "#181713", colorScheme: "dark" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><Providers>{children}</Providers></body></html>;
}
