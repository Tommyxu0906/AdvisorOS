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
      <section className="panel connected">
        <div className="row-between">
          <div>
            <h2>✓ Anthropic connected</h2>
            <p className="muted">
              Model: <strong>{model}</strong>. Usage is billed to your own Anthropic account.
            </p>
          </div>
          <button className="secondary" onClick={disconnect}>
            Disconnect
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="panel">
      <h2>Connect Claude</h2>
      <p className="muted">
        This app runs on your Anthropic API key. Everything below the committee run — the
        financial analysis, the guardrails, the advisor selection — works without one.
      </p>

      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const ok = await connect(draftKey, draftModel);
          if (ok) setDraftKey("");
        }}
      >
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

        <label htmlFor="model">Model</label>
        <select id="model" value={draftModel} onChange={(e) => setDraftModel(e.target.value)}>
          {MODELS.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>

        <button type="submit" disabled={status === "validating" || draftKey.trim().length < 20}>
          {status === "validating" ? "Validating…" : "Connect"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      <p className="fineprint">
        Your key is held in this page's memory only. It is not written to localStorage, a cookie,
        or any server-side store, and it is never included in a saved run. Refreshing the page
        requires re-entering it.
      </p>
    </section>
  );
}
