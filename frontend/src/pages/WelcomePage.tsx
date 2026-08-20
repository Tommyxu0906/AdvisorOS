/**
 * What someone sees before they have typed anything.
 *
 * The old first screen was the intake form — age, dependents, income, employer match as a
 * fraction of salary — which asks for a stranger's finances before telling them what the thing
 * is. This page answers the questions a first-time visitor actually has, in the order they have
 * them, and then offers two doors.
 *
 * The claims here are deliberately narrow. "Deterministic financial checks" is true and
 * checkable; "AI financial advisor" would not be, and "beats the market" would be worse than
 * untrue. Everything stated below is something the product actually does today.
 */

import { navigate } from "../lib/router";

export function WelcomePage({
  onTryDemo,
  onUseMyData,
}: {
  onTryDemo: () => void;
  onUseMyData: () => void;
}) {
  return (
    <div className="welcome">
      <div className="welcome-hero">
        <p className="wordmark" style={{ fontSize: 17, marginBottom: 20 }}>
          AdvisorOS
          <span className="wordmark-sub">Investment decision intelligence</span>
        </p>

        <h1>AI-assisted investment decisions, grounded in financial constraints.</h1>

        <p className="lede">
          Describe a decision you are weighing. Deterministic code checks it against your actual
          numbers first — savings rate, debt cost, concentration, goal horizons — and then a
          committee of distilled investor perspectives argues about what is left.
        </p>

        <div className="welcome-actions">
          <button className="primary large" onClick={onTryDemo}>
            Try the Buffett + Munger demo
          </button>
          <button className="secondary large" onClick={onUseMyData}>
            Use my financial data
          </button>
        </div>

        <p className="small muted" style={{ maxWidth: "58ch" }}>
          The demo needs no account and no API key. The analysis layer is free and runs without
          any AI call; only the committee step uses your own Anthropic key.
        </p>
      </div>

      <div className="trust-row">
        <div className="trust-item">
          <strong>Deterministic financial checks</strong>
          <span>Guardrails and diagnostics are computed in code, not generated.</span>
        </div>
        <div className="trust-item">
          <strong>Read-only architecture</strong>
          <span>No brokerage connection, no order routing, no access to move money.</span>
        </div>
        <div className="trust-item">
          <strong>You bring the AI key</strong>
          <span>Inference bills your Anthropic account. The key is never stored.</span>
        </div>
        <div className="trust-item">
          <strong>No trade execution</strong>
          <span>Output is analysis you act on yourself, or do not.</span>
        </div>
      </div>

      <div className="how-row">
        <div>
          <p className="how-step-index">01</p>
          <h3>Your situation</h3>
          <p className="small muted">
            Income, spending, debts, goals, holdings. Asked once, editable any time, and never
            guessed at on your behalf.
          </p>
        </div>
        <div>
          <p className="how-step-index">02</p>
          <h3>Free analysis</h3>
          <p className="small muted">
            Concentration, reserve coverage, debt pressure and goal conflicts, computed
            deterministically. No AI call, no cost.
          </p>
        </div>
        <div>
          <p className="how-step-index">03</p>
          <h3>The committee</h3>
          <p className="small muted">
            A small team of investor lenses is selected from your gaps, then argues the question
            and is re-checked against the same guardrails.
          </p>
        </div>
        <div>
          <p className="how-step-index">04</p>
          <h3>A decision brief</h3>
          <p className="small muted">
            One conclusion, the candidate actions, what each would cost you, and where the
            committee disagreed.
          </p>
        </div>
      </div>

      <p className="fineprint" style={{ marginTop: 40 }}>
        Educational analysis only, and not personalized investment advice from a licensed advisor.
        Prices shown are exchange-delayed. AdvisorOS does not connect to a brokerage and cannot
        place trades.{" "}
        <button className="linklike" onClick={() => navigate("methodology")}>
          How it works
        </button>
      </p>
    </div>
  );
}
