/**
 * Legacy ApiKeysPage shim.
 *
 * The Keys surface now lives as a tab inside the Settings hub
 * (`/settings?tab=keys`) and its implementation moved to
 * `@/components/api-keys/ApiKeysPanel`. This file is retained only so existing
 * direct-render tests (`import ApiKeysPage from "@/pages/ApiKeysPage"`) keep
 * exercising the real component. The `/api-keys` route is a redirect (see
 * App.tsx) and no longer mounts this page.
 */
export { default } from "@/components/api-keys/ApiKeysPanel";
