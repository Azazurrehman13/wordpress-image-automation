export class ApiError extends Error {
  constructor(public code: string, message: string) { super(message); }
}

async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("backend_unreachable", "Cannot reach the local backend. Start it with scripts\\start.bat.");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = data?.error;
    if (e) throw new ApiError(e.code, e.message);
    const detail = Array.isArray(data?.detail) ? data.detail.map((d: { msg: string }) => d.msg).join("; ") : `HTTP ${res.status}`;
    throw new ApiError("request_invalid", detail);
  }
  return data as T;
}

import type { Batch, Inspection, Job, Match, SearchResult, Settings } from "./types";

export const api = {
  status: () => call<{ connected: boolean; site: string | null; username: string | null; claude_configured: boolean }>("/wordpress/status"),
  connect: (b: { login_url: string; username: string; password: string; headless: boolean }) =>
    call<{ connected: boolean; site: string; username: string; headless: boolean }>("/wordpress/connect", "POST", b),
  logout: () => call<{ connected: boolean }>("/wordpress/logout", "POST"),
  search: (query: string) => call<SearchResult>("/products/search", "POST", { query }),
  inspect: (product_id: number | null, product_url: string | null) =>
    call<Inspection>("/products/inspect", "POST", { product_id, product_url }),
  createJob: (b: { product_name: string; product_id: number | null; product_url: string | null; mode: "update" | "create"; original_source_url: string | null }) =>
    call<Job>("/jobs", "POST", b),
  job: (id: string) => call<Job>(`/jobs/${id}`),
  updateSelection: (id: string, image_ids: number[], best_image_id: number) =>
    call<Job>(`/jobs/${id}/selection`, "PUT", { image_ids, best_image_id }),
  publish: (id: string) => call<Job>(`/jobs/${id}/publish`, "POST"),
  cancel: (id: string) => call<Job>(`/jobs/${id}/cancel`, "POST"),
  startBatch: (products: Match[]) => call<Batch>("/batches", "POST", { products }),
  batch: (id: string) => call<Batch>(`/batches/${id}`),
  cancelBatch: (id: string) => call<Batch>(`/batches/${id}/cancel`, "POST"),
  settings: () => call<Settings>("/settings"),
  saveSettings: (s: Partial<Settings>) => call<Settings>("/settings", "PUT", s),
};