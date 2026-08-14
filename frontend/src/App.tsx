import { useCallback, useEffect, useState } from "react";
import { estimateRun, listAdvisors, runCommittee, selectCommittee } from "./api";
import { AccountControl } from "./components/AccountControl";
import { AdvisorsPanel } from "./components/AdvisorsPanel";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { CommitteePreview } from "./components/CommitteePreview";
import { ConnectPanel } from "./components/ConnectPanel";
import { HistoryPanel } from "./components/HistoryPanel";
import { LivePrices } from "./components/LivePrices";
import { ProfileForm } from "./components/ProfileForm";
import { ReportView } from "./components/ReportView";
import { useAnthropicConnection } from "./context/AnthropicConnectionContext";
import type {
  AdvisorSummary,
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

type View = "analysis" | "advisors" | "history" | "about";

const NAV: { id: View; label: string }[] = [
  { id: "analysis", label: "Analysis" },
  { id: "advisors", label: "Advisors" },
  { id: "history", label: "History" },
  { id: "about", label: "How it works" },
];

export function App() {
  const { isConnected, model, withKey } = useAnthropicConnection();

  const [view, setView] = useState<View>("analysis");
  const [profile, setProfile] = useState(DEFAULT_PROFILE);
  const [portfolio, setPortfolio] = useState(DEFAULT_PORTFOLIO);
  const [question, setQuestion] = useState(
    "Should I sell some NVDA right now and pay off my credit card, or keep riding it?",
  );
  const [depth, setDepth] = useState<AnalysisDepth>("balanced");

  const [advisors, setAdvisors] = useState<AdvisorSummary[]>([]);
  // null = deterministic auto-selection; a non-null set = the user hand-picked the team.
  const [manualIds, setManualIds] = useState<Set<string> | null>(null);

  const [selection, setSelection] = useState<SelectResponse | null>(null);
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [result, setResult] = useState<RunResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAdvisors()
      .then(setAdvisors)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load advisors."));
  }, []);

  function toggleAdvisor(advisorId: string) {
    setManualIds((prev) => {
      const next = new Set(prev ?? []);
      if (next.has(advisorId)) next.delete(advisorId);
      else next.add(advisorId);
      return next.size === 0 ? null : next;
    });
  }

  function onDistilled(advisor: AdvisorSummary) {
    setAdvisors((prev) => [...prev.filter((a) => a.advisor_id !== advisor.advisor_id), advisor]);
    // Fold the freshly distilled advisor straight into the active team.
    setManualIds((prev) => new Set(prev ?? []).add(advisor.advisor_id));
  }

  const manualSelection = manualIds
    ? advisors.filter((a) => manualIds.has(a.advisor_id))
    : null;

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
      const advisorCount = manualSelection ? manualSelection.length : sel.selection.selected.length;
      setEstimate(await estimateRun(depth, advisorCount, model));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed.");
    }
  }, [profile, portfolio, question, depth, model, manualSelection]);

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
          manualIds ? Array.from(manualIds) : null,
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
    <>
      <header className="masthead">
        <div className="masthead-inner">
          <p className="wordmark">AdvisorOS</p>
          <nav>
            {NAV.map((n) => (
              <button
                key={n.id}
                className={`navlink${view === n.id ? " active" : ""}`}
                onClick={() => setView(n.id)}
              >
                {n.label}
              </button>
            ))}
          </nav>
          <span className={`conn-pill${isConnected ? " live" : ""}`}>
            <span className="conn-dot" />
            {isConnected ? model : "Not connected"}
          </span>
          <AccountControl />
        </div>
      </header>

      <div className="app">
        {view === "analysis" && (
          <>
            <div className="page-head">
              <h1>Analysis</h1>
              <p className="lede">
                Deterministic code reads what you enter below and computes the analysis — savings
                rate, debt pressure, portfolio concentration, and a seven-dimension read of where
                you most need help. None of this touches an API or costs anything. Once it looks
                right, head to <strong>Advisors</strong> to pick a team and run the committee
                against it.
              </p>
            </div>

            <ProfileForm
              profile={profile}
              portfolio={portfolio}
              question={question}
              onProfile={setProfile}
              onPortfolio={setPortfolio}
              onQuestion={setQuestion}
            />

            {error && <p className="error">{error}</p>}

            <LivePrices
              symbols={portfolio.holdings.map((h) => h.symbol.trim()).filter(Boolean)}
            />

            {selection && (
              <AnalysisPanel
                analytics={selection.analytics}
                portfolio={selection.portfolio_analytics}
                guardrails={selection.guardrails}
              />
            )}
          </>
        )}

        {view === "advisors" && (
          <>
            <div className="page-head">
              <h1>Advisors</h1>
              <p className="lede">
                Each persona was distilled once from a real investor's writing and public track
                record, then frozen into a reusable profile — mental models, decision rules, and
                declared blind spots. Distilling is the expensive step; running a committee reuses
                the result, so an advisor is never re-researched to answer a question.
              </p>
            </div>

            <ConnectPanel />

            <AdvisorsPanel
              advisors={advisors}
              selectedIds={manualIds}
              onToggle={toggleAdvisor}
              onReset={() => setManualIds(null)}
              onDistilled={onDistilled}
            />

            {error && <p className="error">{error}</p>}

            {selection && (
              <CommitteePreview
                selection={selection.selection}
                manualSelection={manualSelection}
                depth={depth}
                estimate={estimate}
                onDepth={setDepth}
                onRun={onRun}
                running={running}
                canRun={isConnected}
              />
            )}

            {result && <ReportView report={result.report} usage={result.usage} />}
          </>
        )}

        {view === "history" && (
          <>
            <div className="page-head">
              <h1>History</h1>
              <p className="lede">
                Every committee run you paid for and ran while signed in is saved here — the
                question, the report, and what it cost. Nothing about your Anthropic key is ever
                part of it.
              </p>
            </div>
            <HistoryPanel />
          </>
        )}

        {view === "about" && <AboutView />}

        <footer className="panel fineprint">
          Educational analysis only. Not personalized investment advice from a licensed advisor.
          Prices are exchange-delayed and shown for analysis. AdvisorOS does not connect to a
          brokerage or place trades.
        </footer>
      </div>
    </>
  );
}

function AboutView() {
  return (
    <>
      <div className="page-head">
        <h1>How it works</h1>
        <p className="lede">
          Three ideas govern the design: the model proposes and reasons, code calculates and
          decides, and you own the credentials and the cost.
        </p>
      </div>

      <section className="panel">
        <h2>Code decides, the model reasons</h2>
        <p>
          Most of this product runs without an API call. Your savings rate, emergency-fund
          coverage, debt ratios, portfolio concentration, and the seven-dimension profile of where
          you most need help are computed in plain Python. So is advisor routing: each persona
          carries a scored expertise vector, and the selector picks the smallest team that covers
          your gaps. You can change your situation all day and watch the analysis update without
          spending a cent.
        </p>
        <p>
          Claude is called for exactly four things — reading intent out of your question, advisor
          analysis, cross-examination, and final synthesis. Financial guardrails are never left to
          the model: blocking conditions like a thin emergency reserve or high-APR debt are
          computed in code, injected into the prompt as hard constraints, and the final report is
          re-validated against them afterwards.
        </p>
      </section>

      <section className="panel">
        <h2>What distillation actually does</h2>
        <p>
          An advisor is not a prompt that says "answer like Warren Buffett." It is produced by a
          multi-stage pipeline that runs <em>once</em>: a planner proposes research questions
          aimed at how the subject makes decisions and where they fail, several research passes
          run concurrently against those questions, and a synthesis pass compresses the findings
          into a single structured profile. Every stage returns schema-constrained JSON, never
          free text.
        </p>
        <p>
          What comes out is a typed artifact rather than a personality: a scored expertise vector
          the router consumes directly, plus mental models, heuristics, reasoning rules, declared
          blind spots, and the questions the persona will decline to answer. Validation code then
          rejects the result outright if it scores zero everywhere — that persona could never be
          selected — or if it declares no blind spots, on the view that a persona claiming no
          limits has no business in front of someone's finances.
        </p>
        <p>
          The full manifest is kept for provenance, but committee runs send only a compressed
          profile of roughly 1,200 tokens. That split is the point: distillation is expensive and
          happens once, while reuse is cheap and happens on every question.
        </p>
      </section>

      <section className="panel">
        <h2>Why you bring the key</h2>
        <p>
          Inference is billed to your Anthropic account, not to whoever hosts this. The server
          holds no API key of its own and has no fallback credential to charge — a committee run
          without a key is simply unavailable rather than quietly billed elsewhere. Your key
          lives in this page's memory for the session, is passed per-request, and is never
          written to a database, a log line, or a saved run.
        </p>
        <p>
          Cost is shown rather than hidden. Before a run you get the stage count and an estimate;
          afterwards you get actual token counts and per-stage, per-advisor attribution. The three
          depth modes exist to make the tradeoff explicit — Quick runs independent analysis and one
          synthesis, Balanced adds cross-examination and a risk challenge, Deep adds revised memos
          after critique.
        </p>
      </section>
    </>
  );
}
