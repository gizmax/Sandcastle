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
  private initPromise: Promise<void> | null = null;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
    // Probe backend on first load
    this.initPromise = this.probe();
  }

  private async probe(): Promise<void> {
    try {
      const res = await fetch(`${this.baseUrl}/health`, { signal: AbortSignal.timeout(2000) });
      if (!res.ok) throw new Error("unhealthy");
      const data = await res.json();
      if (data?.data?.status !== "ok") throw new Error("unhealthy");
      console.info("[Sandcastle] Backend connected");
    } catch {
      console.info("[Sandcastle] Backend unavailable, using demo data");
      this.useMock = true;
    }
  }

  private async ensureInit(): Promise<void> {
    if (this.initPromise) {
      await this.initPromise;
      this.initPromise = null;
    }
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

  private mock<T>(
    path: string,
    params?: Record<string, string>,
    method?: string,
    body?: unknown
  ): ApiResponse<T> {
    return mockFetch(path, params, method, body) as ApiResponse<T>;
  }

  async get<T>(path: string, params?: Record<string, string>): Promise<ApiResponse<T>> {
    await this.ensureInit();
    if (this.useMock) return this.mock<T>(path, params);

    try {
      const url = new URL(`${this.baseUrl}${path}`, window.location.origin);
      if (params) {
        Object.entries(params).forEach(([k, v]) => {
          if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
        });
      }
      const res = await fetch(url.toString(), { headers: this.headers() });
      return this.handleResponse<T>(res);
    } catch {
      // Only fall back to mock on actual network errors (backend unreachable)
      console.info(`[Sandcastle] Backend unavailable, using demo data`);
      this.useMock = true;
      return this.mock<T>(path, params);
    }
  }

  private async handleResponse<T>(res: Response): Promise<ApiResponse<T>> {
    if (!res.ok) {
      try {
        return await res.json();
      } catch {
        return { data: null, error: { code: `HTTP_${res.status}`, message: res.statusText } };
      }
    }
    return res.json();
  }

  async post<T>(path: string, body?: unknown): Promise<ApiResponse<T>> {
    await this.ensureInit();
    if (this.useMock) {
      return this.mock<T>(path, undefined, "POST", body);
    }

    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "POST",
        headers: this.headers(),
        body: body ? JSON.stringify(body) : undefined,
      });
      return this.handleResponse<T>(res);
    } catch {
      this.useMock = true;
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }
  }

  async patch<T>(path: string, body: unknown): Promise<ApiResponse<T>> {
    await this.ensureInit();
    if (this.useMock) {
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }

    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "PATCH",
        headers: this.headers(),
        body: JSON.stringify(body),
      });
      return this.handleResponse<T>(res);
    } catch {
      this.useMock = true;
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }
  }

  async delete<T>(path: string): Promise<ApiResponse<T>> {
    await this.ensureInit();
    if (this.useMock) {
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }

    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "DELETE",
        headers: this.headers(),
      });
      return this.handleResponse<T>(res);
    } catch {
      this.useMock = true;
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }
  }

  sseConnect(path: string): { url: string; headers: HeadersInit } {
    return {
      url: `${this.baseUrl}${path}`,
      headers: this.headers(),
    };
  }
}

export const api = new ApiClient(API_BASE_URL);
export type { ApiResponse };
