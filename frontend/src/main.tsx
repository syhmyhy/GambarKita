import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./App";
import { ToastProvider } from "./components/ui/Toast";
import "./index.css";

// A deploy replaced the page chunks this tab refers to: reload once to pick up the new build.
window.addEventListener("vite:preloadError", (event) => {
  try {
    if (sessionStorage.getItem("gk.reloaded-for-chunk")) return;
    sessionStorage.setItem("gk.reloaded-for-chunk", "1");
  } catch {
    return;
  }
  event.preventDefault();
  window.location.reload();
});

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <App />
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>,
);
