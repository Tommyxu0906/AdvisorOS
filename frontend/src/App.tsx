import { useCallback, useEffect, useState } from "react";
import { estimateRun, listAdvisors, runCommittee, selectCommittee } from "./api";
import { AccountControl } from "./components/AccountControl";
import { AdvisorsPanel } from "./components/AdvisorsPanel";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { CommitteePreview } from "./components/CommitteePreview";
import { ConnectPanel } from "./components/ConnectPanel";
import { HistoryPanel } from "./components/HistoryPanel";
import { HoldingsEditor } from "./components/HoldingsEditor";
import { IntakePage, IntakeSignInNote } from "./components/IntakePage";
import { LivePrices } from "./components/LivePrices";
import { ReportView } from "./components/ReportView";
import { SaveBadge, SettingsPage } from "./components/SettingsPage";
import { useAnthropicConnection } from "./context/AnthropicConnectionContext";
import { useAuth } from "./context/AuthContext";
import type { HoldingDraft, ProfileDraft } from "./lib/draft";
import {
  EMPTY_PORTFOLIO,
  EMPTY_PROFILE,
  missingFields,
  toPortfolioInput,
  toProfileInput,
} from "./lib/draft";
import { useQuotes } from "./lib/useQuotes";
import { useSavedProfile } from "./lib/useSavedProfile";
import type {
  AdvisorSummary,
  AnalysisDepth,
  EstimateResponse,
  RunResponse,
  SelectResponse,
} from "./types";

type View = "analysis" | "advisors" | "history" | "settings" | "about";

const NAV: { id: View; label: string }[] = [
  { id: "analysis", label: "Analysis" },
  { id: "advisors", label: "Advisors" },
  { id: "history", label: "History" },
  { id: "settings", label: "Settings" },
  { id: "about", label: "How it works" },
];

export function App() {
  const { isConnected, model, withKey } = useAnthropicConnection();
  const { user } = useAuth();

  const [view, setView] = useState<View>("analysis");
  // null until we know what the account holds. Decided once, then only the Continue button
  // clears it: deriving it from the live field check instead would make the intake page vanish
  // out from under someone the instant they typed the last character.
  const [intakeDone, setIntakeDone] = useState<boolean | null>(null);
  const [profile, setProfile] = useState<ProfileDraft>(EMPTY_PROFILE);
  const [holdings, setHoldings] = useState<HoldingDraft[]>(EMPTY_PORTFOLIO.holdings);
  const [question, setQuestion] = useState("");
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

  const quotes = useQuotes(
    holdings.map((h) => h.symbol.trim().toUpperCase()).filter(Boolean),
  );

  const saved = useSavedProfile(profile, holdings, (p, h) => {
    setProfile(p);
    setHoldings(h);
    // A stored profile means this account has already been through intake.
    setIntakeDone(true);
  });

  // Wait for `ready` before deciding: mid-load a returning user is indistinguishable from a new
  // one, and asking them to re-enter a profile the server is about to return would be worse
  // than a moment of blank screen.
  useEffect(() => {
    if (!saved.ready || intakeDone !== null) return;
    setIntakeDone(missingFields(profile).length === 0);
    // Deliberately keyed on readiness alone. This answers "did this visitor arrive with a
    // usable profile", which is a question asked once, not re-asked as they type.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved.ready]);

  const needsIntake = intakeDone === false;

  // null until every answer the analysis cannot invent has actually been given.
  const profileInput = toProfileInput(profile);
  const portfolioInput = toPortfolioInput(holdings);
  const ready = profileInput !== null && question.trim() !== "";

  // The deterministic half runs eagerly and for free, whether or not a key is connected.
  const refreshAnalysis = useCallback(async () => {
    if (!profileInput || !question.trim()) {
      setSelection(null);
      setEstimate(null);
      return;
    }
    setError(null);
    try {
      const sel = await selectCommittee(
        profileInput,
        portfolioInput.holdings.length ? portfolioInput : null,
        question,
        depth,
      );
      setSelection(sel);
      const advisorCount = manualSelection ? manualSelection.length : sel.selection.selected.length;
      setEstimate(await estimateRun(depth, advisorCount, model));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed.");
    }
    // profileInput/portfolioInput are rebuilt every render, so the deps are their JSON rather
    // than the objects themselves — otherwise this refetches on every keystroke anywhere.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    JSON.stringify(profileInput),
    JSON.stringify(portfolioInput),
    question,
    depth,
    model,
    manualSelection,
  ]);

  useEffect(() => {
    const t = setTimeout(refreshAnalysis, 300);
    return () => clearTimeout(t);
  }, [refreshAnalysis]);

  async function onRun() {
    if (!profileInput) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const response = await withKey((key) =>
        runCommittee(
          key,
          profileInput,
          portfolioInput.holdings.length ? portfolioInput : null,
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
          {/* Navigation is hidden during intake: there is nothing worth looking at on the
              other pages until the situation exists. */}
          {!needsIntake && (
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
          )}
          <span className={`conn-pill${isConnected ? " live" : ""}`}>
            <span className="conn-dot" />
            {isConnected ? model : "Not connected"}
          </span>
          <AccountControl />
        </div>
      </header>

      <div className="app">
        {needsIntake && (
          <>
            <IntakePage
              profile={profile}
              signedIn={!!user}
              onProfile={setProfile}
              onDone={() => setIntakeDone(true)}
            />
            <IntakeSignInNote />
          </>
        )}

        {!needsIntake && view === "analysis" && (
          <>
            <div className="page-head">
              <h1>Analysis</h1>
              <p className="lede">
                Your holdings and your question. Everything else about your situation was
                collected once and lives under <strong>Settings</strong>. Deterministic code
                reads it all and computes savings rate, debt pressure, portfolio concentration,
                and a seven-dimension read of where you most need help — none of which touches an
                API or costs anything. When it looks right, head to <strong>Advisors</strong> to
                run a committee against it.
              </p>
            </div>

            <section className="panel">
              <div className="row-between">
                <h2>Your portfolio</h2>
                <SaveBadge saved={saved} incomplete={missingFields(profile).length > 0} />
              </div>

              {saved.error && <p className="error">{saved.error}</p>}

              <HoldingsEditor holdings={holdings} quotes={quotes} onHoldings={setHoldings} />

              <label htmlFor="question">Your question</label>
              <textarea
                id="question"
                rows={3}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Should I sell some NVDA to pay off my credit card?"
              />
            </section>

            {error && <p className="error">{error}</p>}

            <LivePrices state={quotes} />

            {selection && (
              <AnalysisPanel
                analytics={selection.analytics}
                portfolio={selection.portfolio_analytics}
                guardrails={selection.guardrails}
              />
            )}
          </>
        )}

        {!needsIntake && view === "advisors" && (
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

            {!ready && (
              <section className="panel">
                <h2>Committee</h2>
                <p className="muted">
                  {missingFields(profile).length > 0 ? (
                    <>
                      Your situation is incomplete — fill in{" "}
                      {missingFields(profile).join(", ")} under <strong>Settings</strong>.
                    </>
                  ) : (
                    <>
                      Ask a question on the <strong>Analysis</strong> page first. The committee is
                      selected from your actual numbers and what you are asking about, so there is
                      nothing to route on until both exist.
                    </>
                  )}
                </p>
              </section>
            )}

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

        {!needsIntake && view === "history" && (
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

        {!needsIntake && view === "settings" && (
          <SettingsPage profile={profile} saved={saved} onProfile={setProfile} />
        )}

        {!needsIntake && view === "about" && <AboutView />}

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
