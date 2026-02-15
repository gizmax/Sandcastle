import { API_BASE_URL } from "@/lib/constants";
import { mockFetch } from "@/api/mock";

interface ApiResponse<T = unknown> {
  data: T | null;
  error: { code: string; message: string } | null;
  meta?: { total: number; limit: number; offset: number } | null;
}

class ApiClient {
  private baseUrl: string;
  private apiKey: string | null = null;
  private useMock = false;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  setApiKey(key: string | null) {
    this.apiKey = key;
  }

  private headers(): HeadersInit {
    const h: HeadersInit = { "Content-Type": "application/json" };
    if (this.apiKey) {
      h["X-API-Key"] = this.apiKey;
    }
    return h;
  }

  private mock<T>(path: string, params?: Record<string, string>): ApiResponse<T> {
    return mockFetch(path, params) as ApiResponse<T>;
  }

  async get<T>(path: string, params?: Record<string, string>): Promise<ApiResponse<T>> {
    if (this.useMock) return this.mock<T>(path, params);

    try {
      const url = new URL(`${this.baseUrl}${path}`, window.location.origin);
      if (params) {
        Object.entries(params).forEach(([k, v]) => {
          if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
        });
      }
      const res = await fetch(url.toString(), { headers: this.headers() });
      if (!res.ok) {
        // Return structured error for HTTP errors - do NOT fall back to mock
        try {
          return await res.json();
        } catch {
          return { data: null, error: { code: `HTTP_${res.status}`, message: res.statusText } };
        }
      }
      return res.json();
    } catch {
      // Only fall back to mock on actual network errors (backend unreachable)
      console.info(`[Sandcastle] Backend unavailable, using demo data`);
      this.useMock = true;
      return this.mock<T>(path, params);
    }
  }

  async post<T>(path: string, body?: unknown): Promise<ApiResponse<T>> {
    if (this.useMock) {
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }

    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "POST",
        headers: this.headers(),
        body: body ? JSON.stringify(body) : undefined,
      });
      return res.json();
    } catch {
      this.useMock = true;
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }
  }

  async patch<T>(path: string, body: unknown): Promise<ApiResponse<T>> {
    if (this.useMock) {
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }

    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "PATCH",
        headers: this.headers(),
        body: JSON.stringify(body),
      });
      return res.json();
    } catch {
      this.useMock = true;
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }
  }

  async delete<T>(path: string): Promise<ApiResponse<T>> {
    if (this.useMock) {
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }

    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "DELETE",
        headers: this.headers(),
      });
      return res.json();
    } catch {
      this.useMock = true;
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }
  }

  sseUrl(path: string): string {
    const base = `${this.baseUrl}${path}`;
    // EventSource cannot send custom headers, so pass the API key as a query param
    if (this.apiKey) {
      const sep = base.includes("?") ? "&" : "?";
      return `${base}${sep}token=${encodeURIComponent(this.apiKey)}`;
    }
    return base;
  }
}

export const api = new ApiClient(API_BASE_URL);
export type { ApiResponse };
