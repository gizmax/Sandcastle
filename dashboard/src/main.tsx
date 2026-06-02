import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";
import "./index.css";
import App from "./App.tsx";

// Apply the theme immediately to prevent a flash. Dark-first: a stored preference
// wins; new visitors default to dark unless the OS explicitly prefers light. Keep this
// in sync with getInitialTheme() in hooks/useTheme.ts.
const savedTheme = localStorage.getItem("theme");
const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
if (savedTheme === "dark" || (!savedTheme && !prefersLight)) {
  document.documentElement.classList.add("dark");
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
    <Toaster position="bottom-right" richColors closeButton />
  </StrictMode>
);
