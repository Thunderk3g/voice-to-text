"use client";

import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import { swrConfig } from "@/lib/swr";

export function SWRProvider({ children }: { children: ReactNode }): JSX.Element {
  return <SWRConfig value={swrConfig}>{children}</SWRConfig>;
}

export default SWRProvider;
