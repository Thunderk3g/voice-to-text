// Thin HTTP wrapper. All paths go through the Next rewrite at /api/v2t.
// Set NEXT_PUBLIC_API_BASE_URL at build time; it's read inside next.config.js.

const BROWSER_PREFIX = "/api/v2t";

function joinPath(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  const normalized = path.startsWith("/") ? path : `/${path}`;
  // On the server (SSR), bypass the rewrite and hit the API directly when possible.
  if (typeof window === "undefined") {
    const direct = process.env.NEXT_PUBLIC_API_BASE_URL;
    if (direct) return `${direct.replace(/\/$/, "")}${normalized}`;
  }
  return `${BROWSER_PREFIX}${normalized}`;
}

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, message: string, body: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, `HTTP ${res.status} for ${res.url}`, body);
  }
  if (res.status === 204) return undefined as unknown as T;
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(joinPath(path), {
    ...init,
    method: "GET",
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  return handle<T>(res);
}

export async function apiPost<T, B = unknown>(
  path: string,
  body: B,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(joinPath(path), {
    ...init,
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  return handle<T>(res);
}

// Multipart upload. Do NOT set Content-Type — the browser sets the
// multipart/form-data boundary automatically from the FormData body.
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(joinPath(path), {
    method: "POST",
    headers: { Accept: "application/json" },
    body: form,
    cache: "no-store",
  });
  return handle<T>(res);
}

// SWR fetcher (GET only)
export const swrFetcher = <T>(path: string): Promise<T> => apiGet<T>(path);
