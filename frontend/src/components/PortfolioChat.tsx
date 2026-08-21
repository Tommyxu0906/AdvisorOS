/**
 * The consultation, sitting beside the holdings it is about.
 *
 * One conversation is open at a time and there is no picker for the others: every completed
 * round is written to the user's Reports, which is where past conversations are read. A dropdown
 * would have been a second, worse history — shorter, unlabelled, and gone on refresh.
 *
 * Putting it here rather than on its own page is the whole point: the question is almost always
 * about a specific row in the table to the left, and having both on screen means the answer can
 * be checked against the position without navigating away.
 *
 * It behaves like any chat — type, press Enter, get an answer, ask a follow-up — and underneath
 * it is not one. Two differences that matter:
 *
 * **Every message recomputes the scenario server-side.** The endpoint takes the profile and the
 * portfolio and runs the policy engine again before any framework is asked anything. The
 * conversation cannot drift away from the real numbers, because it is re-derived from them on
 * each turn rather than carried along in the transcript.
 *
 * **Each conversation carries its own committee.** Asking about a concentrated position and
 * asking about an emergency fund are different consultations, and one global committee spanning
 * both would produce a transcript where half the participants had no business answering.
 */

import { useEffect, useRef, useState } from "react";
import type { AdvisorSummary, ChatTurn, ConsultDepth, DecisionCandidate } from "../types";
import type { Conversation } from "../lib/conversations";
import { Advanced, InlineAlert, StatusBadge } from "../ui";
import { ConnectKeyButton } from "./ConnectKeyButton";
import { LensCard } from "./LensCard";

/** Each level adds a real round of model calls, so the ordering is also a cost ordering. */
const DEPTH_OPTIONS: { id: ConsultDepth; label: string; calls: string }[] = [
  { id: "quick", label: "Quick", calls: "1 round" },
  { id: "balanced", label: "Balanced", calls: "2 rounds" },
  { id: "deep", label: "Deep", calls: "3 rounds" },
];

const DEPTH_HINT: Record<ConsultDepth, string> = {
  quick: "Each framework answers independently",
  balanced: "They then read each other and may revise",
  deep: "They also argue against their own position",
};

const SUGGESTIONS = [
  "Is this portfolio too concentrated?",
  "Should I trim NVDA to pay down debt?",
  "What would you want to know that my numbers don't tell you?",
];

export function PortfolioChat({
  conversations,
  activeId,
  advisors,
  running,
  error,
  candidates,
  mockLLM,
  isConnected,
  profileReady,
  signedIn,
  onNewChat,
  onToggleAdvisor,
  onAsk,
  depth,
  onDepth,
}: {
  conversations: Conversation[];
  activeId: string;
  advisors: AdvisorSummary[];
  running: boolean;
  error: string | null;
  candidates: DecisionCandidate[];
  mockLLM: boolean;
  isConnected: boolean;
  profileReady: boolean;
  onNewChat: () => void;
  signedIn: boolean;
  onToggleAdvisor: (conversationId: string, advisorId: string) => void;
  onAsk: (question: string) => void;
  depth: ConsultDepth;
  onDepth: (d: ConsultDepth) => void;
}) {
  const active = conversations.find((c) => c.id === activeId) ?? null;
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  const selected = active?.advisorIds ?? [];
  const canSend = profileReady && !running && draft.trim().length > 0 && selected.length > 0;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [active?.turns.length, running]);

  function send() {
    if (!canSend) return;
    onAsk(draft.trim());
    setDraft("");
  }

  return (
    <aside className="pchat" aria-label="Consult the committee">
      <header className="pchat-head">
        <div className="row-between" style={{ gap: 8 }}>
          <h2 className="pchat-title">Consult the committee</h2>
          {mockLLM ? (
            <StatusBadge tone="warn">Demo answers</StatusBadge>
          ) : isConnected ? (
            <StatusBadge tone="info">Bills your key</StatusBadge>
          ) : (
            <StatusBadge tone="neutral">Not connected</StatusBadge>
          )}
        </div>

        <div className="pchat-convs">
          <span className="tiny muted" style={{ flex: 1, minWidth: 0 }}>
            {signedIn
              ? "Saved to Reports as you go."
              : "Sign in to keep this in your Reports."}
          </span>
          <button
            type="button"
            className="secondary pchat-new"
            onClick={onNewChat}
            disabled={running}
          >
            New chat
          </button>
        </div>

        <Advanced label={`Advisors in this chat — ${selected.length} selected`}>
          <div className="pchat-advisors">
            {advisors.map((a) => (
              <label
                key={a.advisor_id}
                className={`advisor-chip${selected.includes(a.advisor_id) ? " selected" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={selected.includes(a.advisor_id)}
                  disabled={running || !active}
                  onChange={() => active && onToggleAdvisor(active.id, a.advisor_id)}
                />
                <span>{a.display_name}</span>
              </label>
            ))}
          </div>
          <p className="tiny muted" style={{ margin: "8px 0 0" }}>
            Each conversation keeps its own selection. Frameworks distilled from public writing —
            not the people themselves.
          </p>
        </Advanced>
      </header>

      <div className="pchat-thread">
        {!profileReady ? (
          <p className="small muted">
            Add your situation under Settings first — the committee reasons about your actual
            numbers, so there has to be a profile before there is anything to consult about.
          </p>
        ) : !active || active.turns.length === 0 ? (
          <div className="pchat-empty">
            <p className="small" style={{ marginTop: 0 }}>
              Ask about anything in the table. The engine computes the options first; the
              frameworks you picked then argue about those options rather than inventing their own.
            </p>
            <div className="pchat-suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="pchat-suggestion" onClick={() => setDraft(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          active.turns.map((turn, i) => <Turn key={i} turn={turn} />)
        )}

        {candidates.length > 0 && active && active.turns.length > 0 && (
          <p className="tiny muted pchat-candidates">
            Options on the table: {candidates.map((c) => c.label).join(" · ")}
          </p>
        )}

        {running && (
          <p className="pchat-thinking" role="status">
            Recomputing your scenario, then asking each framework…
          </p>
        )}
        <div ref={endRef} />
      </div>

      <div className="pchat-composer">
        {error && (
          <InlineAlert tone="risk" title="That didn't go through">
            {error}
          </InlineAlert>
        )}

        {!isConnected && !mockLLM ? (
          <InlineAlert
            tone="info"
            title="Connect a key to consult"
            action={<ConnectKeyButton label="Connect" />}
          >
            Everything in the table was computed without one.
          </InlineAlert>
        ) : (
          <>
            <textarea
              id="pchat-input"
              rows={2}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                // Enter sends, Shift+Enter breaks the line — the convention everyone already
                // has in their fingers from every other chat.
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={
                selected.length === 0 ? "Select an advisor above first…" : "Ask the committee…"
              }
              aria-label="Your question"
              disabled={!profileReady || running}
            />
            <div className="pchat-controls">
              <label className="visually-hidden" htmlFor="pchat-depth">
                How many rounds
              </label>
              <select
                id="pchat-depth"
                value={depth}
                onChange={(e) => onDepth(e.target.value as ConsultDepth)}
                disabled={running}
                title={DEPTH_HINT[depth]}
              >
                {DEPTH_OPTIONS.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.label} · {d.calls}
                  </option>
                ))}
              </select>
              <button type="button" className="primary" onClick={send} disabled={!canSend}>
                {running ? "Asking…" : "Ask"}
              </button>
            </div>
            <p className="tiny muted" style={{ margin: "6px 0 0" }}>
              {selected.length === 0
                ? "No advisor selected"
                : `${DEPTH_HINT[depth]} · Enter to send`}
            </p>
          </>
        )}
      </div>
    </aside>
  );
}

function Turn({ turn }: { turn: ChatTurn }) {
  if (turn.role === "user") {
    return (
      <div className="pchat-user">
        <p>{turn.text}</p>
      </div>
    );
  }

  if (turn.assumption) {
    return (
      <div className="assumption-applied">
        <p className="metric-label" style={{ margin: 0 }}>
          Assumption applied — scenario recomputed
        </p>
        {turn.assumption.map((c) => (
          <p key={c.label} className="tiny" style={{ margin: "3px 0 0" }}>
            <strong>{c.label}</strong> {c.from} → {c.to}
          </p>
        ))}
      </div>
    );
  }

  return (
    <div className="pchat-answer">
      {turn.advisor_responses.map((r) => (
        <LensCard key={r.advisor_id} response={r} />
      ))}
      {turn.synthesis && (
        <div className="pchat-synthesis">
          <p className="metric-label" style={{ margin: "0 0 4px" }}>
            Where that leaves it
          </p>
          <p className="small" style={{ margin: 0 }}>
            {turn.synthesis.headline}
          </p>
          {turn.synthesis.overrides.map((o) => (
            <p key={o} className="tiny muted" style={{ margin: "6px 0 0" }}>
              {o}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
