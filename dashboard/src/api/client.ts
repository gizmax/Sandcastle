import { API_BASE_URL } from "@/lib/constants";
import { mockFetch } from "@/api/mock";

interface ApiResponse<T = unknown> {
  data: T | null;
  error: { code: string; message: string } | null;
  meta?: { total: number; limit: number; offset: number } | null;
}

const REQUEST_TIMEOUT = 15_000; // 15 seconds

class ApiClient {
  private baseUrl: string;
  private apiKey: string | null = null;
  private useMock = false;
  private initPromise: Promise<void> | null = null;
  private _mockListeners: Array<(mock: boolean) => void> = [];

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
    // Restore saved API key before probing
    const savedKey = localStorage.getItem("sandcastle_api_key");
    if (savedKey) this.apiKey = savedKey;
    // Probe backend on first load
    this.initPromise = this.probe();
  }

  private async probe(): Promise<void> {
    try {
      const res = await fetch(`${this.baseUrl}/health`, {
        headers: this.headers(),
        signal: AbortSignal.timeout(2000),
      });
      if (!res.ok) throw new Error("unhealthy");
      const data = await res.json();
      const s = data?.data?.status;
      if (s !== "ok" && s !== "degraded") throw new Error("unhealthy");
      console.info("[Sandcastle] Backend connected");
    } catch {
      console.info("[Sandcastle] Backend unavailable, using demo data");
      this.setMock(true);
    }
  }

  private async ensureInit(): Promise<void> {
    if (this.initPromise) {
      await this.initPromise;
      this.initPromise = null;
    }
  }

  get isMockMode(): boolean {
    return this.useMock;
  }

  onMockChange(cb: (mock: boolean) => void): () => void {
    this._mockListeners.push(cb);
    return () => {
      this._mockListeners = this._mockListeners.filter((l) => l !== cb);
    };
  }

  private setMock(value: boolean) {
    if (this.useMock !== value) {
      this.useMock = value;
      this._mockListeners.forEach((cb) => cb(value));
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
      const res = await fetch(url.toString(), {
        headers: this.headers(),
        signal: AbortSignal.timeout(REQUEST_TIMEOUT),
      });
      return this.handleResponse<T>(res);
    } catch {
      // Only fall back to mock on actual network errors (backend unreachable)
      console.info(`[Sandcastle] Backend unavailable, using demo data`);
      this.setMock(true);
      return this.mock<T>(path, params);
    }
  }

  private async handleResponse<T>(res: Response): Promise<ApiResponse<T>> {
    if (!res.ok) {
      try {
        const json = await res.json();
        // FastAPI wraps HTTPException detail in {"detail": ...} - unwrap it
        if (json.detail && typeof json.detail === "object" && "error" in json.detail) {
          return json.detail;
        }
        // Handle simple string detail from FastAPI
        if (json.detail && typeof json.detail === "string") {
          return { data: null, error: { code: `HTTP_${res.status}`, message: json.detail } };
        }
        return json;
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
        signal: AbortSignal.timeout(REQUEST_TIMEOUT),
      });
      return this.handleResponse<T>(res);
    } catch {
      this.setMock(true);
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
        signal: AbortSignal.timeout(REQUEST_TIMEOUT),
      });
      return this.handleResponse<T>(res);
    } catch {
      this.setMock(true);
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
        signal: AbortSignal.timeout(REQUEST_TIMEOUT),
      });
      return this.handleResponse<T>(res);
    } catch {
      this.setMock(true);
      return { data: { message: "Demo mode - action simulated" } as T, error: null };
    }
  }

  /**
   * Build an SSE-compatible URL with token query parameter for auth.
   * EventSource does not support custom headers, so the API key is passed
   * as a query parameter instead.
   */
  sseUrl(path: string): string {
    const url = new URL(`${this.baseUrl}${path}`, window.location.origin);
    if (this.apiKey) {
      url.searchParams.set("token", this.apiKey);
    }
    return url.toString();
  }
}

export const api = new ApiClient(API_BASE_URL);
export type { ApiResponse };
