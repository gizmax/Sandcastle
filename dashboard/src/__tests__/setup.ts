import { afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";

// Mock localStorage
const store: Record<string, string> = {};
const localStorageMock = {
  getItem: (key: string) => store[key] ?? null,
  setItem: (key: string, value: string) => { store[key] = value; },
  removeItem: (key: string) => { delete store[key]; },
  clear: () => { Object.keys(store).forEach((k) => delete store[k]); },
  get length() { return Object.keys(store).length; },
  key: (index: number) => Object.keys(store)[index] ?? null,
};

Object.defineProperty(globalThis, "localStorage", { value: localStorageMock });

// Mock sessionStorage (used by api/client.ts for API key storage)
const sessionStore: Record<string, string> = {};
const sessionStorageMock = {
  getItem: (key: string) => sessionStore[key] ?? null,
  setItem: (key: string, value: string) => { sessionStore[key] = value; },
  removeItem: (key: string) => { delete sessionStore[key]; },
  clear: () => { Object.keys(sessionStore).forEach((k) => delete sessionStore[k]); },
  get length() { return Object.keys(sessionStore).length; },
  key: (index: number) => Object.keys(sessionStore)[index] ?? null,
};

Object.defineProperty(globalThis, "sessionStorage", { value: sessionStorageMock });

// Mock matchMedia
Object.defineProperty(globalThis, "matchMedia", {
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Mock import.meta.env
if (typeof import.meta !== "undefined") {
  (import.meta as unknown as Record<string, unknown>).env = {
    ...(import.meta as unknown as Record<string, Record<string, unknown>>).env,
    VITE_API_URL: "/api",
  };
}

// Polyfill AbortSignal.timeout for jsdom
if (!AbortSignal.timeout) {
  AbortSignal.timeout = (ms: number) => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("TimeoutError", "TimeoutError")), ms);
    return controller.signal;
  };
}

// Reset storage between tests
afterEach(() => {
  localStorageMock.clear();
  sessionStorageMock.clear();
});
