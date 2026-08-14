import { useState } from "react";
import { useAnthropicConnection } from "../context/AnthropicConnectionContext";

const MODELS = [
  { id: "claude-opus-5", label: "Claude Opus 5 — most capable" },
  { id: "claude-sonnet-5", label: "Claude Sonnet 5 — balanced" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5 — cheapest" },
];

export function ConnectPanel() {
  const { status, model, error, isConnected, connect, disconnect } = useAnthropicConnection();
  const [draftKey, setDraftKey] = useState("");
  const [draftModel, setDraftModel] = useState(model);

  if (isConnected) {
    return (
      <section className="panel">
        <div className="row-between">
          <h2>Claude engine</h2>
          <span className="badge free">connected</span>
        </div>
        <p className="muted">
          Running <strong>{model}</strong>. Every token is billed to your own Anthropic account;
          this app never holds credentials of its own.
        </p>
        <button className="secondary" onClick={disconnect}>
          Disconnect
        </button>
      </section>
    );
  }

  return (
    <section className="panel">
      <h2>Claude engine</h2>
      <p className="muted">
        Reasoning runs on your key. Everything else on this page — the financial analysis, the
        guardrails, the advisor selection — is computed without one.
      </p>

      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const ok = await connect(draftKey, draftModel);
          if (ok) setDraftKey("");
        }}
      >
        <div className="connect-grid">
          <div className="field">
            <label htmlFor="apikey">Anthropic API key</label>
            <input
              id="apikey"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder="sk-ant-..."
              value={draftKey}
              onChange={(e) => setDraftKey(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="model">Model</label>
            <select id="model" value={draftModel} onChange={(e) => setDraftModel(e.target.value)}>
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>

          <button type="submit" disabled={status === "validating" || draftKey.trim().length < 20}>
            {status === "validating" ? "Validating…" : "Connect"}
          </button>
        </div>
      </form>

      {error && <p className="error">{error}</p>}

      <p className="fineprint">
        The key is held in this page's memory only — never written to localStorage, a cookie, or
        any server-side store, and never included in a saved run. Refreshing requires re-entry.
      </p>
    </section>
  );
}
