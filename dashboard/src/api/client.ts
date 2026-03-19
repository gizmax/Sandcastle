import { API_BASE_URL } from "@/lib/constants";
import { mockFetch } from "@/api/mock";

interface ApiResponse<T = unknown> {
  data: T | null;
  error: { code: string; message: string } | null;
  meta?: { total: number; limit: number; offset: number } | null;
}

const REQUEST_TIMEOUT = 15_000; // 15 seconds
const MAX_RETRIES = 2;
const RETRY_DELAY_MS = 1000;

// Use sessionStorage instead of localStorage for API keys to reduce XSS
// exposure. sessionStorage is cleared when the tab closes, limiting the
// window for token theft. localStorage is still checked as a migration
// fallback so existing users are not forced to re-enter their key.
const SESSION_KEY = "sandcastle_api_key";
const LEGACY_LOCAL_KEY = "sandcastle_api_key";

function readStoredKey(): string | null {
  // Prefer sessionStorage (more secure)
  const sessionKey = sessionStorage.getItem(SESSION_KEY);
  if (sessionKey) return sessionKey;
  // Fall back to localStorage for users who authenticated before this change
  const localKey = localStorage.getItem(LEGACY_LOCAL_KEY);
  if (localKey) {
    // Migrate: copy to sessionStorage and remove from localStorage
    sessionStorage.setItem(SESSION_KEY, localKey);
    localStorage.removeItem(LEGACY_LOCAL_KEY);
    return localKey;
  }
  return null;
}

function storeKey(key: string): void {
  sessionStorage.setItem(SESSION_KEY, key);
  // Ensure the legacy localStorage key is cleaned up
  localStorage.removeItem(LEGACY_LOCAL_KEY);
}

function clearStoredKey(): void {
  sessionStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(LEGACY_LOCAL_KEY);
}

/** Check if an HTTP status code is retryable (server error or rate limit). */
function isRetryableStatus(status: number): boolean {
  return status >= 500 || status === 429;
}

/** Wait for a specified number of milliseconds. */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class ApiClient {
  private baseUrl: string;
  private apiKey: string | null = null;
  private useMock = false;
  private initPromise: Promise<void> | null = null;
  private _mockListeners: Array<(mock: boolean) => void> = [];
  private _inflightRequests = new Map<string, Promise<ApiResponse<unknown>>>();

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
    // Restore saved API key before probing
    const savedKey = readStoredKey();
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
      this.setMock(false);
      console.info("[Sandcastle] Backend connected");
    } catch {
      console.info("[Sandcastle] Backend unavailable, using demo data");
      this.setMock(true);
    }
  }

  /** Re-probe the backend and exit mock mode if it's back. */
  async reprobeBackend(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/health`, {
        headers: this.headers(),
        signal: AbortSignal.timeout(3000),
      });
      if (!res.ok) return false;
      const data = await res.json();
      const s = data?.data?.status;
      if (s === "ok" || s === "degraded") {
        this.setMock(false);
        console.info("[Sandcastle] Backend reconnected, exiting demo mode");
        return true;
      }
    } catch { /* still down */ }
    return false;
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

  /** Check whether an API key is currently stored in session/local storage. */
  hasStoredKey(): boolean {
    return readStoredKey() !== null;
  }

  /** Read the stored API key prefix for display (first 8 chars). */
  storedKeyPrefix(): string | null {
    const key = readStoredKey();
    return key ? key.slice(0, 8) : null;
  }

  /** Retrieve the full stored API key (for auth validation probes). */
  getStoredKey(): string | null {
    return readStoredKey();
  }

  /** Persist an API key to sessionStorage (not localStorage). */
  storeApiKey(key: string): void {
    storeKey(key);
  }

  /** Remove the stored API key from all storage. */
  clearStoredKey(): void {
    clearStoredKey();
  }

  private headers(): HeadersInit {
    const h: HeadersInit = { "Content-Type": "application/json" };
    if (this.apiKey) {
      h["X-API-Key"] = this.apiKey;
    }
    return h;
  }

  /**
   * Auth headers only (no Content-Type). For SSE/streaming fetch requests
   * where the API key should travel in headers, not URL parameters.
   */
  authHeaders(): HeadersInit {
    const h: HeadersInit = {};
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

  /**
   * Execute a fetch request with retry logic for 5xx and 429 responses.
   * Returns the Response on success, or throws on exhausted retries / network error.
   */
  private async fetchWithRetry(
    input: string,
    init: RequestInit,
    retries: number = MAX_RETRIES
  ): Promise<Response> {
    let lastResponse: Response | null = null;
    for (let attempt = 0; attempt <= retries; attempt++) {
      const res = await fetch(input, init);
      if (!isRetryableStatus(res.status) || attempt === retries) {
        return res;
      }
      lastResponse = res;
      // Wait before retrying, with exponential backoff
      const backoff = RETRY_DELAY_MS * Math.pow(2, attempt);
      console.info(
        `[Sandcastle] Request to ${input} returned ${res.status}, retrying in ${backoff}ms (attempt ${attempt + 1}/${retries})`
      );
      await delay(backoff);
    }
    // This should not be reached, but return lastResponse as safety net
    return lastResponse!;
  }

  async get<T>(path: string, params?: Record<string, string>): Promise<ApiResponse<T>> {
    await this.ensureInit();
    if (this.useMock) return this.mock<T>(path, params);

    // Build a deduplication key for GET requests
    const url = new URL(`${this.baseUrl}${path}`, window.location.origin);
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
      });
    }
    const dedupeKey = `GET:${url.toString()}`;

    // Return existing in-flight request if one is pending (request deduplication)
    const existing = this._inflightRequests.get(dedupeKey);
    if (existing) {
      return existing as Promise<ApiResponse<T>>;
    }

    const requestPromise = (async (): Promise<ApiResponse<T>> => {
      try {
        const res = await this.fetchWithRetry(url.toString(), {
          headers: this.headers(),
          signal: AbortSignal.timeout(REQUEST_TIMEOUT),
        });
        return this.handleResponse<T>(res);
      } catch {
        // Return error instead of permanently switching to mock mode.
        // Mock mode is only entered via the initial probe or explicit reprobeBackend().
        return { data: null, error: { code: "NETWORK_ERROR", message: "Backend unreachable" } };
      } finally {
        this._inflightRequests.delete(dedupeKey);
      }
    })();

    this._inflightRequests.set(dedupeKey, requestPromise as Promise<ApiResponse<unknown>>);
    return requestPromise;
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
        // Validate response structure before returning - ensure it has the
        // expected shape to prevent downstream code from reading undefined fields
        if (json && typeof json === "object" && ("data" in json || "error" in json)) {
          return json;
        }
        // Unknown error shape - wrap it
        return { data: null, error: { code: `HTTP_${res.status}`, message: res.statusText } };
      } catch {
        return { data: null, error: { code: `HTTP_${res.status}`, message: res.statusText } };
      }
    }
    return res.json();
  }

  async post<T>(path: string, body?: unknown, timeoutMs?: number): Promise<ApiResponse<T>> {
    await this.ensureInit();
    if (this.useMock) {
      return this.mock<T>(path, undefined, "POST", body);
    }

    try {
      const res = await this.fetchWithRetry(
        `${this.baseUrl}${path}`,
        {
          method: "POST",
          headers: this.headers(),
          body: body ? JSON.stringify(body) : undefined,
          signal: AbortSignal.timeout(timeoutMs ?? REQUEST_TIMEOUT),
        },
        // Only retry idempotent-safe requests; POST mutations get 0 retries
        0
      );
      return this.handleResponse<T>(res);
    } catch {
      return { data: null, error: { code: "NETWORK_ERROR", message: "Backend unreachable" } };
    }
  }

  async patch<T>(path: string, body: unknown): Promise<ApiResponse<T>> {
    await this.ensureInit();
    if (this.useMock) {
      return this.mock<T>(path, undefined, "PATCH", body);
    }

    try {
      const res = await this.fetchWithRetry(
        `${this.baseUrl}${path}`,
        {
          method: "PATCH",
          headers: this.headers(),
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(REQUEST_TIMEOUT),
        },
        0
      );
      return this.handleResponse<T>(res);
    } catch {
      return { data: null, error: { code: "NETWORK_ERROR", message: "Backend unreachable" } };
    }
  }

  async put<T>(path: string, body: unknown): Promise<ApiResponse<T>> {
    await this.ensureInit();
    if (this.useMock) {
      return this.mock<T>(path, undefined, "PUT", body);
    }

    try {
      const res = await this.fetchWithRetry(
        `${this.baseUrl}${path}`,
        {
          method: "PUT",
          headers: this.headers(),
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(REQUEST_TIMEOUT),
        },
        0
      );
      return this.handleResponse<T>(res);
    } catch {
      return { data: null, error: { code: "NETWORK_ERROR", message: "Backend unreachable" } };
    }
  }

  async delete<T>(path: string): Promise<ApiResponse<T>> {
    await this.ensureInit();
    if (this.useMock) {
      return this.mock<T>(path, undefined, "DELETE");
    }

    try {
      const res = await this.fetchWithRetry(
        `${this.baseUrl}${path}`,
        {
          method: "DELETE",
          headers: this.headers(),
          signal: AbortSignal.timeout(REQUEST_TIMEOUT),
        },
        0
      );
      return this.handleResponse<T>(res);
    } catch {
      return { data: null, error: { code: "NETWORK_ERROR", message: "Backend unreachable" } };
    }
  }

  async uploadFile(file: File): Promise<ApiResponse<{ path: string; filename: string; size: number }>> {
    await this.ensureInit();
    if (this.useMock) {
      return {
        data: { path: `/tmp/mock-uploads/${file.name}`, filename: file.name, size: file.size },
        error: null,
      };
    }

    try {
      const form = new FormData();
      form.append("file", file);
      const headers: HeadersInit = {};
      if (this.apiKey) headers["X-API-Key"] = this.apiKey;
      // Do NOT set Content-Type - browser sets multipart boundary automatically
      const res = await fetch(`${this.baseUrl}/upload`, {
        method: "POST",
        headers,
        body: form,
        signal: AbortSignal.timeout(60_000),
      });
      return this.handleResponse(res);
    } catch {
      return { data: null, error: { code: "NETWORK_ERROR", message: "Upload failed" } };
    }
  }

  /** Upload a file for use as a workflow input field.
   *
   * Returns a file_id that can be stored as a field value (prefixed with @upload:).
   * Do NOT set Content-Type header - the browser sets the multipart boundary automatically.
   */
  async upload(file: File): Promise<ApiResponse<{ file_id: string; filename: string; content_type: string; size_bytes: number; storage: string; url: string | null }>> {
    await this.ensureInit();
    if (this.useMock) {
      return {
        data: {
          file_id: `upload_${Date.now().toString(36)}`,
          filename: file.name,
          content_type: file.type || "application/octet-stream",
          size_bytes: file.size,
          storage: "local",
          url: null,
        },
        error: null,
      };
    }

    try {
      const formData = new FormData();
      formData.append("file", file);
      const headers: HeadersInit = {};
      if (this.apiKey) headers["X-API-Key"] = this.apiKey;
      // Do NOT set Content-Type - browser sets multipart boundary automatically
      const res = await fetch(`${this.baseUrl}/upload`, {
        method: "POST",
        headers,
        body: formData,
        signal: AbortSignal.timeout(60_000),
      });
      return this.handleResponse(res);
    } catch {
      return { data: null, error: { code: "NETWORK_ERROR", message: "Upload failed" } };
    }
  }

  /**
   * Build an SSE URL with token query parameter for auth.
   * @deprecated Use fetch() with authHeaders() instead. This method puts the
   * API key in the URL query string which leaks it into server logs and
   * browser history. Kept only for backward compatibility with existing tests.
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
