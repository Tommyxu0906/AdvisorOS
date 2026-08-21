/**
 * Routing and the state the pages share. Everything else moved out.
 *
 * This file used to hold five inline page bodies, the About copy, and the layout. Now it owns
 * three things a page cannot: which route is showing, the profile/portfolio/question the whole
 * workflow operates on, and the two API calls that produce the analysis and the run.
 *
 * The demo path is the piece worth reading carefully. `demo` is a separate flag rather than a
 * pre-filled profile, because autosave has to be suppressed while it is on — otherwise a
 * signed-in visitor who clicks "Try a demo portfolio" comes back tomorrow to a sample
 * household's balance sheet saved as their own.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  consultCommittee,
  estimateRun,
  getHealth,
  listAdvisors,
  runCommittee,
  selectCommittee,
} from "./api";
import { useAnthropicConnection } from "./context/AnthropicConnectionContext";
import { useAuth } from "./context/AuthContext";
import type { AssumptionChange } from "./components/AssumptionPanel";
import type { HoldingDraft, ProfileDraft } from "./lib/draft";
import {
  EMPTY_PORTFOLIO,
  EMPTY_PROFILE,
  missingFields,
  toPortfolioInput,
  toProfileInput,
} from "./lib/draft";
import { demoHoldings, demoProfile, DEMO_QUESTION } from "./lib/demo";
import type { Conversation } from "./lib/conversations";
import { historyFor, newConversation, titleFrom } from "./lib/conversations";
import { navigate, useRoute } from "./lib/router";
import { useQuotes } from "./lib/useQuotes";
import { useSavedProfile } from "./lib/useSavedProfile";
import { AppShell } from "./shell/AppShell";
import { DecisionWorkspace } from "./pages/DecisionWorkspace";
import { InvestorLibraryPage } from "./pages/InvestorLibraryPage";
import { MethodologyPage } from "./pages/MethodologyPage";
import { OnboardingFlow } from "./pages/OnboardingFlow";
import { PortfolioPage } from "./pages/PortfolioPage";
import { ChatPage } from "./pages/ChatPage";
import { WelcomePage } from "./pages/WelcomePage";
import { HistoryPanel } from "./components/HistoryPanel";
import { SettingsPage } from "./components/SettingsPage";
import type {
  AdvisorSummary,
  AnalysisDepth,
  ChatTurn,
  DecisionCandidate,
  EstimateResponse,
  RunResponse,
  SelectResponse,
} from "./types";

/** The two lenses the demo convenes. Built-in manifests — nothing is distilled to answer. */
const CONSULT_ADVISORS = ["buffett", "munger"];

/** Never reaches Anthropic: only sent when the server reported it is serving canned answers. */
const DEMO_PLACEHOLDER_KEY = "sk-ant-" + "demo".repeat(12);

export function App() {
  const { model, withKey, isConnected } = useAnthropicConnection();
  const { user } = useAuth();
  const route = useRoute();

  const [profile, setProfile] = useState<ProfileDraft>(EMPTY_PROFILE);
  const [holdings, setHoldings] = useState<HoldingDraft[]>(EMPTY_PORTFOLIO.holdings);
  const [question, setQuestion] = useState("");
  const [depth, setDepth] = useState<AnalysisDepth>("balanced");
  const [demo, setDemo] = useState(false);

  const [advisors, setAdvisors] = useState<AdvisorSummary[]>([]);
  const [manualIds, setManualIds] = useState<Set<string> | null>(null);

  const [selection, setSelection] = useState<SelectResponse | null>(null);
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [result, setResult] = useState<RunResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Consultation state lives here and nowhere else: no table, no migration, no persistence.
  // It is discarded on refresh for the same reason the API key is.
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [candidates, setCandidates] = useState<DecisionCandidate[]>([]);
  const [consulting, setConsulting] = useState(false);
  const [consultError, setConsultError] = useState<string | null>(null);
  // True when the server was started with AIFA_MOCK_LLM=1. It then answers with canned text and
  // needs no key — which the interface must state plainly rather than pass off as a real run.
  const [mockLLM, setMockLLM] = useState(false);
  // Set for exactly one profile change: the one the user asked for by pressing Apply. Without
  // it the clearing effect below would throw away the very transcript the change is meant to be
  // compared against.
  const applying = useRef<AssumptionChange[] | null>(null);

  // Conversations live for the session only — see lib/conversations.ts on why nothing persists.
  const [conversations, setConversations] = useState<Conversation[]>(() => [newConversation()]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [chatRunning, setChatRunning] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [chatCandidates, setChatCandidates] = useState<DecisionCandidate[]>([]);

  useEffect(() => {
    getHealth()
      .then((h) => setMockLLM(h.mock_llm))
      .catch(() => setMockLLM(false));
  }, []);

  // null until we know what the account holds — see the readiness effect below.
  const [known, setKnown] = useState<boolean | null>(null);

  useEffect(() => {
    listAdvisors()
      .then(setAdvisors)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load advisors."));
  }, []);

  const quotes = useQuotes(
    holdings.map((h) => h.symbol.trim().toUpperCase()).filter(Boolean),
  );

  const saved = useSavedProfile(profile, holdings, (p, h) => {
    setProfile(p);
    setHoldings(h);
    setKnown(true);
  });

  // Wait for `ready` before deciding: mid-load a returning user is indistinguishable from a new
  // one, and sending them to Welcome would be worse than a moment of blank screen.
  useEffect(() => {
    if (!saved.ready || known !== null) return;
    const complete = missingFields(profile).length === 0;
    setKnown(complete);
    if (!complete && route !== "methodology") navigate("welcome");
    // Keyed on readiness alone: this asks "did this visitor arrive with a usable profile", which
    // is a question asked once, not re-asked on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved.ready]);

  function startDemo() {
    setDemo(true);
    setProfile(demoProfile());
    setHoldings(demoHoldings());
    setQuestion(DEMO_QUESTION);
    setKnown(true);
    navigate("decision");
  }

  function leaveDemo() {
    setDemo(false);
    setProfile(EMPTY_PROFILE);
    setHoldings([]);
    setQuestion("");
    setResult(null);
    navigate("onboarding");
  }

  const profileInput = toProfileInput(profile);
  const portfolioInput = toPortfolioInput(holdings);

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
      const count = manualIds ? manualIds.size : sel.selection.selected.length;
      setEstimate(await estimateRun(depth, count, model));
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
    manualIds,
  ]);

  useEffect(() => {
    const t = setTimeout(refreshAnalysis, 300);
    return () => clearTimeout(t);
  }, [refreshAnalysis]);

  // A changed question or a changed balance sheet is a different decision, and carrying the old
  // transcript into it would have the committee answering about a scenario that no longer holds.
  useEffect(() => {
    const pending = applying.current;
    applying.current = null;

    if (pending) {
      // Keep the conversation and record the move, so the next answers can be read against the
      // earlier ones rather than appearing out of nowhere.
      setTurns((prev) => [
        ...prev,
        {
          role: "committee",
          text: "",
          advisor_responses: [],
          assumption: pending,
        },
      ]);
      return;
    }

    setTurns([]);
    setCandidates([]);
    setConsultError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question, JSON.stringify(profileInput), JSON.stringify(portfolioInput)]);

  useEffect(() => {
    if (activeChatId === null && conversations.length > 0) setActiveChatId(conversations[0].id);
  }, [activeChatId, conversations]);

  function onNewChat() {
    const created = newConversation();
    setConversations((prev) => [created, ...prev]);
    setActiveChatId(created.id);
    setChatCandidates([]);
    setChatError(null);
  }

  function onDeleteChat(id: string) {
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== id);
      if (id === activeChatId) setActiveChatId(next[0]?.id ?? null);
      return next.length ? next : [newConversation()];
    });
  }

  function onToggleChatAdvisor(conversationId: string, advisorId: string) {
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== conversationId
          ? c
          : {
              ...c,
              advisorIds: c.advisorIds.includes(advisorId)
                ? c.advisorIds.filter((a) => a !== advisorId)
                : [...c.advisorIds, advisorId],
            },
      ),
    );
  }

  async function onChatAsk(text: string) {
    const conversation = conversations.find((c) => c.id === activeChatId);
    if (!conversation || !profileInput) return;

    // The question lands in the transcript before the request goes out, so the thread reads in
    // the order it happened rather than appearing all at once when the answer arrives.
    const history = historyFor(conversation);
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== conversation.id
          ? c
          : {
              ...c,
              title: c.turns.length === 0 ? titleFrom(text) : c.title,
              turns: [...c.turns, { role: "user", text, advisor_responses: [] }],
            },
      ),
    );

    setChatRunning(true);
    setChatError(null);
    try {
      const call = (key: string) =>
        consultCommittee(
          key,
          profileInput,
          portfolioInput.holdings.length ? portfolioInput : null,
          text,
          conversation.advisorIds,
          history,
          model,
        );
      const response = mockLLM ? await call(DEMO_PLACEHOLDER_KEY) : await withKey(call);

      setChatCandidates(response.candidates);
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== conversation.id
            ? c
            : {
                ...c,
                turns: [
                  ...c.turns,
                  {
                    role: "committee",
                    text: response.synthesis.headline,
                    advisor_responses: response.responses,
                    synthesis: response.synthesis,
                  },
                ],
              },
        ),
      );
    } catch (e) {
      setChatError(e instanceof Error ? e.message : "The consultation failed.");
    } finally {
      setChatRunning(false);
    }
  }

  function onApplyAssumption(next: ProfileDraft, changes: AssumptionChange[]) {
    applying.current = changes;
    setProfile(next);
  }

  async function onConsult(userQuestion: string) {
    if (!profileInput) return;
    setConsulting(true);
    setConsultError(null);

    // The user's turn lands immediately; the committee's arrives when it answers.
    const asked: ChatTurn = { role: "user", text: userQuestion, advisor_responses: [] };
    const priorHistory = turns.map((turn) => ({
      role: turn.role,
      text: turn.text,
      advisor_responses: turn.advisor_responses,
    }));
    setTurns((prev) => [...prev, asked]);

    try {
      const call = (key: string) =>
        consultCommittee(
          key,
          profileInput,
          portfolioInput.holdings.length ? portfolioInput : null,
          userQuestion,
          CONSULT_ADVISORS,
          priorHistory,
          model,
        );
      // One call path either way: in demo mode the server ignores the key entirely.
      const response = mockLLM ? await call(DEMO_PLACEHOLDER_KEY) : await withKey(call);
      setCandidates(response.candidates);
      setTurns((prev) => [
        ...prev,
        {
          role: "committee",
          text: "",
          advisor_responses: response.responses,
          synthesis: response.synthesis,
        },
      ]);
    } catch (e) {
      setConsultError(e instanceof Error ? e.message : "The consultation failed.");
    } finally {
      setConsulting(false);
    }
  }

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
    setManualIds((prev) => new Set(prev ?? []).add(advisor.advisor_id));
  }

  // Welcome and onboarding are deliberately outside the shell: a navigation rail offering five
  // destinations to someone who has not said what the product is yet is noise.
  if (route === "welcome") {
    return (
      <WelcomePage onTryDemo={startDemo} onUseMyData={() => navigate("onboarding")} />
    );
  }

  if (route === "onboarding") {
    return (
      <OnboardingFlow
        profile={profile}
        holdings={holdings}
        quotes={quotes}
        onProfile={setProfile}
        onHoldings={setHoldings}
        onDone={() => {
          setKnown(true);
          navigate("decision");
        }}
      />
    );
  }

  return (
    <AppShell route={route} demo={demo} onExitDemo={demo ? leaveDemo : undefined}>
      {route === "decision" && (
        <DecisionWorkspace
          profile={profile}
          holdings={holdings}
          question={question}
          depth={depth}
          selection={selection}
          estimate={estimate}
          advisors={advisors}
          manualIds={manualIds}
          result={result}
          running={running}
          error={error}
          demo={demo}
          onQuestion={setQuestion}
          onDepth={setDepth}
          onToggleAdvisor={toggleAdvisor}
          onResetAdvisors={() => setManualIds(null)}
          onRun={onRun}
          turns={turns}
          candidates={candidates}
          consulting={consulting}
          consultError={consultError}
          onConsult={onConsult}
          mockLLM={mockLLM}
          onApplyAssumption={onApplyAssumption}
        />
      )}

      {route === "chat" && (
        <ChatPage
          conversations={conversations}
          activeId={activeChatId}
          advisors={advisors}
          profile={profile}
          running={chatRunning}
          error={chatError}
          candidates={chatCandidates}
          mockLLM={mockLLM}
          isConnected={isConnected}
          onNewChat={onNewChat}
          onSelectChat={setActiveChatId}
          onDeleteChat={onDeleteChat}
          onToggleAdvisor={onToggleChatAdvisor}
          onAsk={onChatAsk}
        />
      )}

      {route === "portfolio" && (
        <PortfolioPage
          holdings={holdings}
          quotes={quotes}
          demo={demo}
          onHoldings={setHoldings}
        />
      )}

      {route === "investors" && (
        <InvestorLibraryPage advisors={advisors} onDistilled={onDistilled} />
      )}

      {route === "reports" && (
        <>
          <div className="page-head">
            <h1>Reports</h1>
            <p className="lede">
              Every committee run you made while signed in — the question, the brief, and what it
              cost. Nothing about your Anthropic key is ever part of a saved run.
            </p>
          </div>
          {user ? (
            <HistoryPanel />
          ) : (
            <p className="muted">Sign in to keep a history of your runs.</p>
          )}
        </>
      )}

      {route === "settings" && (
        <SettingsPage profile={profile} saved={saved} onProfile={setProfile} />
      )}

      {route === "methodology" && <MethodologyPage />}

      <footer className="fineprint">
        Educational analysis only. Not personalized investment advice from a licensed advisor.
        Prices are exchange-delayed and shown for analysis. AdvisorOS does not connect to a
        brokerage or place trades.
      </footer>
    </AppShell>
  );
}
