import type { SWRConfiguration } from "swr";
import { swrFetcher } from "./api";

export const swrConfig: SWRConfiguration = {
  fetcher: swrFetcher,
  revalidateOnFocus: false,
  revalidateIfStale: true,
  shouldRetryOnError: false,
  dedupingInterval: 5_000,
};
