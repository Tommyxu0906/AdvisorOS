import { useCallback, useEffect, useState } from "react";
import { estimateRun, runCommittee, selectCommittee } from "./api";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { CommitteePreview } from "./components/CommitteePreview";
import { ConnectPanel } from "./components/ConnectPanel";
import { ProfileForm } from "./components/ProfileForm";
import { ReportView } from "./components/ReportView";
import { useAnthropicConnection } from "./context/AnthropicConnectionContext";
import type {
  AnalysisDepth,
  EstimateResponse,
  PortfolioInput,
  ProfileInput,
  RunResponse,
  SelectResponse,
} from "./types";

const DEFAULT_PROFILE: ProfileInput = {
  age: 34,
  dependents: 1,
  income: { annual_gross: 145000, annual_net: null, stability: 0.7, employer_match_pct: 0.04 },
  expenses: { monthly_essential: 4200, monthly_discretionary: 1500 },
  debts: [{ name: "credit card", balance: 9000, apr: 0.229, minimum_monthly_payment: 280 }],
  assets: [
    { name: "savings", value: 11000, account_type: "cash", is_liquid: true },
    { name: "401k", value: 88000, account_type: "traditional_401k", is_liquid: false },
  ],
  goals: [
    { name: "house down payment", goal_type: "home_purchase", years_until_needed: 2, priority: 1 },
  ],
  risk_tolerance: "moderate_aggressive",
  self_reported_experience: 0.35,
  notes: "",
};

const DEFAULT_PORTFOLIO: PortfolioInput = {
  holdings: [
    { symbol: "NVDA", asset_class: "us_equity", market_value: 60000 },
    { symbol: "VTI", asset_class: "us_equity", market_value: 28000 },
  ],
};

export function App() {
  const { isConnected, model, withKey } = useAnthropicConnection();

  const [profile, setProfile] = useState(DEFAULT_PROFILE);
  const [portfolio, setPortfolio] = useState(DEFAULT_PORTFOLIO);
  const [question, setQuestion] = useState(
    "Should I sell some NVDA right now and pay off my credit card, or keep riding it?",
  );
  const [depth, setDepth] = useState<AnalysisDepth>("balanced");

  const [selection, setSelection] = useState<SelectResponse | null>(null);
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [result, setResult] = useState<RunResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The deterministic half runs eagerly and for free, whether or not a key is connected.
  const refreshAnalysis = useCallback(async () => {
    setError(null);
    try {
      const sel = await selectCommittee(
        profile,
        portfolio.holdings.length ? portfolio : null,
        question,
        depth,
      );
      setSelection(sel);
      setEstimate(await estimateRun(depth, sel.selection.selected.length, model));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed.");
    }
  }, [profile, portfolio, question, depth, model]);

  useEffect(() => {
    const t = setTimeout(refreshAnalysis, 300);
    return () => clearTimeout(t);
  }, [refreshAnalysis]);

  async function onRun() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const response = await withKey((key) =>
        runCommittee(
          key,
          profile,
          portfolio.holdings.length ? portfolio : null,
          question,
          depth,
          model,
        ),
      );
      setResult(response);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The committee run failed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>AIFinancialAdvisor</h1>
        <p className="muted">
          A committee of distilled investor personas reasons about your finances. The math and
          the routing are deterministic; the reasoning runs on your own Anthropic key.
        </p>
      </header>

      <ConnectPanel />

      <ProfileForm
        profile={profile}
        portfolio={portfolio}
        question={question}
        onProfile={setProfile}
        onPortfolio={setPortfolio}
        onQuestion={setQuestion}
      />

      {error && <p className="error">{error}</p>}

      {selection && (
        <>
          <AnalysisPanel
            analytics={selection.analytics}
            portfolio={selection.portfolio_analytics}
            guardrails={selection.guardrails}
          />
          <CommitteePreview
            selection={selection.selection}
            depth={depth}
            estimate={estimate}
            onDepth={setDepth}
            onRun={onRun}
            running={running}
            canRun={isConnected}
          />
        </>
      )}

      {result && <ReportView result={result} />}

      <footer className="fineprint">
        Educational analysis only. Not personalized investment advice from a licensed advisor.
      </footer>
    </div>
  );
}
