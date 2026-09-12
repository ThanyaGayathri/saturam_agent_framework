// Frontend entry: mounts the React tree and global providers.
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { DemoProfileProvider } from "./context/DemoProfileContext";
import { PrototypeAuthProvider } from "./context/PrototypeAuthContext";
import { ThemeProvider } from "./context/ThemeContext";
import { KpiRegistryProvider } from "./context/KpiRegistryContext";
import { TourProvider } from "./tours/TourContext";
import TourOverlay from "./tours/TourOverlay";
import { initThemeOnStartup } from "./hooks/useTheme";
import "./index.css";

initThemeOnStartup();

/**
 * When a new deploy replaces hashed asset chunks, a browser that still holds
 * the previous index.html can request a chunk name that no longer exists
 * (the server returns the SPA fallback index.html with text/html MIME —
 * "Failed to load module script … expected module got text/html"). Catch
 * both the sync ChunkLoadError name and the "Failed to fetch dynamically
 * imported module" message, mark the reload in sessionStorage so we don't
 * loop, and force a hard reload once to pick up the new index.html.
 */
if (typeof window !== "undefined") {
  const RELOAD_MARK = "retailgpt:reloaded-for-chunk-error";
  const isChunkLoadFailure = (err) => {
    const msg = err && (err.message || String(err));
    if (!msg) return false;
    return (
      /ChunkLoadError/i.test(msg) ||
      /Failed to fetch dynamically imported module/i.test(msg) ||
      /Loading chunk .* failed/i.test(msg) ||
      /Failed to load module script/i.test(msg) ||
      /Importing a module script failed/i.test(msg)
    );
  };
  const forceReloadOnce = () => {
    try {
      if (sessionStorage.getItem(RELOAD_MARK)) return;
      sessionStorage.setItem(RELOAD_MARK, "1");
    } catch {
      /* ignore quota / disabled storage */
    }
    // Standard reload() respects cached index.html, which is exactly what
    // is stale here (browser has an old HTML with old chunk hashes and the
    // chunks were GC'd on Vercel). Bust the cache by replacing the URL with
    // a fresh query param, then strip it after the reload so the URL stays
    // clean.
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("_r", Date.now().toString());
      window.location.replace(url.toString());
    } catch {
      window.location.reload();
    }
  };
  // Strip the cache-busting query param on the fresh load so the URL bar
  // stays clean and shareable.
  try {
    if (window.location.search.includes("_r=")) {
      const url = new URL(window.location.href);
      url.searchParams.delete("_r");
      const clean = url.pathname + (url.search ? url.search : "") + url.hash;
      window.history.replaceState(window.history.state, "", clean);
    }
  } catch {
    /* ignore */
  }
  window.addEventListener("error", (event) => {
    if (isChunkLoadFailure(event.error) || isChunkLoadFailure(event)) {
      forceReloadOnce();
    }
  });
  window.addEventListener("unhandledrejection", (event) => {
    if (isChunkLoadFailure(event.reason)) {
      forceReloadOnce();
    }
  });
  // Clear the reload mark once the page has loaded successfully.
  window.addEventListener("load", () => {
    try {
      sessionStorage.removeItem(RELOAD_MARK);
    } catch {
      /* ignore */
    }
  });
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <PrototypeAuthProvider>
        <ThemeProvider>
          <DemoProfileProvider>
            <KpiRegistryProvider>
              <TourProvider>
                <App />
                <TourOverlay />
              </TourProvider>
            </KpiRegistryProvider>
          </DemoProfileProvider>
        </ThemeProvider>
      </PrototypeAuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
