import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AnthropicConnectionProvider } from "./context/AnthropicConnectionContext";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AnthropicConnectionProvider>
      <App />
    </AnthropicConnectionProvider>
  </StrictMode>,
);
