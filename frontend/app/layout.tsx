import "./globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Sidebar } from "@/components/Sidebar";
import { SWRProvider } from "@/components/SWRProvider";

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
    <html lang="en">
      <body className="min-h-screen bg-ink-50 text-ink-900">
        <SWRProvider>
          <div className="flex min-h-screen">
            <Sidebar />
            <main className="flex-1 overflow-x-hidden">
              <div className="mx-auto max-w-7xl px-6 py-6">{children}</div>
            </main>
          </div>
        </SWRProvider>
      </body>
    </html>
  );
}
