import "./globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Fraunces, Schibsted_Grotesk, JetBrains_Mono } from "next/font/google";
import { Sidebar } from "@/components/Sidebar";
import { SWRProvider } from "@/components/SWRProvider";

const display = Fraunces({
  subsets: ["latin"],
  variable: "--font-display",
  axes: ["opsz", "SOFT", "WONK"],
});

const body = Schibsted_Grotesk({
  subsets: ["latin"],
  variable: "--font-body",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "v2t — Call Intelligence",
  description: "Multilingual insurance call intelligence dashboard.",
};

export default function RootLayout({
  children,
}: {
  children: ReactNode;
}): JSX.Element {
  return (
    <html
      lang="en"
      className={`${display.variable} ${body.variable} ${mono.variable}`}
    >
      <body className="min-h-screen bg-ink-50 font-sans text-ink-800">
        <SWRProvider>
          <div className="flex min-h-screen">
            <Sidebar />
            <main className="flex-1 overflow-x-hidden">
              <div className="mx-auto max-w-7xl px-8 py-8">{children}</div>
            </main>
          </div>
        </SWRProvider>
      </body>
    </html>
  );
}
