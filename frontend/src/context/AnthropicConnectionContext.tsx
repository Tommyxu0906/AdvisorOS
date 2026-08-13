/**
 * BYOK connection state.
 *
 * The API key lives in a single `useState` and nowhere else. There is deliberately no
 * localStorage, no sessionStorage, no cookie, and no URL parameter. Refreshing the page loses
 * the key and the user re-enters it. That is the intended trade: a key that is never written
 * down cannot be read out of a device later.
 *
 * CI enforces this — see the "Assert the key is never persisted client-side" step in ci.yml.
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { validateKey } from "../api";

export type ConnectionStatus = "disconnected" | "validating" | "connected" | "error";

interface ConnectionState {
  status: ConnectionStatus;
  model: string;
  error: string | null;
  /** True when an LLM-backed action is allowed. */
  isConnected: boolean;
  connect: (key: string, model: string) => Promise<boolean>;
  disconnect: () => void;
  /**
   * Read the key for a single outbound request. Named to make call sites obvious in review —
   * anything calling this is sending the key somewhere.
   */
  withKey: <T>(fn: (key: string) => Promise<T>) => Promise<T>;
}

const AnthropicConnectionContext = createContext<ConnectionState | null>(null);

export function AnthropicConnectionProvider({ children }: { children: ReactNode }) {
  // The only copy of the key in the application.
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [model, setModel] = useState("claude-opus-5");
  const [error, setError] = useState<string | null>(null);

  const connect = useCallback(async (key: string, selectedModel: string) => {
    const trimmed = key.trim();
    setStatus("validating");
    setError(null);
    try {
      const result = await validateKey(trimmed, selectedModel);
      if (!result.valid) {
        setApiKey(null);
        setStatus("error");
        setError(result.error ?? "Anthropic authentication failed.");
        return false;
      }
      setApiKey(trimmed);
      setModel(selectedModel);
      setStatus("connected");
      return true;
    } catch (e) {
      setApiKey(null);
      setStatus("error");
      setError(e instanceof Error ? e.message : "Could not reach the server.");
      return false;
    }
  }, []);

  const disconnect = useCallback(() => {
    setApiKey(null);
    setStatus("disconnected");
    setError(null);
  }, []);

  const withKey = useCallback(
    async <T,>(fn: (key: string) => Promise<T>): Promise<T> => {
      if (!apiKey) {
        throw new Error("Connect an Anthropic API key first.");
      }
      return fn(apiKey);
    },
    [apiKey],
  );

  const value = useMemo<ConnectionState>(
    () => ({
      status,
      model,
      error,
      isConnected: status === "connected" && apiKey !== null,
      connect,
      disconnect,
      withKey,
    }),
    [status, model, error, apiKey, connect, disconnect, withKey],
  );

  return (
    <AnthropicConnectionContext.Provider value={value}>
      {children}
    </AnthropicConnectionContext.Provider>
  );
}

export function useAnthropicConnection(): ConnectionState {
  const ctx = useContext(AnthropicConnectionContext);
  if (!ctx) {
    throw new Error("useAnthropicConnection must be used inside AnthropicConnectionProvider");
  }
  return ctx;
}
