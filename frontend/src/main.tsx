import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

document.body.style.margin = "0";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
