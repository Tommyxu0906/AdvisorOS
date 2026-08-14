import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AnthropicConnectionProvider } from "./context/AnthropicConnectionContext";
import { AuthProvider } from "./context/AuthContext";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <AnthropicConnectionProvider>
        <App />
      </AnthropicConnectionProvider>
    </AuthProvider>
  </StrictMode>,
);
