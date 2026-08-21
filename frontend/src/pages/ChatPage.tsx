/**
 * The consultation surface: conversations on the left, one thread on the right.
 *
 * Shaped like a chat because that is the right shape for asking a question and then asking a
 * better one after reading the answer. The previous arrangement made the question a form field
 * on a dashboard, and the follow-up input did not exist until an analysis had already run — so
 * the first thing a visitor saw was a page with nothing to type into.
 *
 * What it deliberately is *not* is a general assistant. Two things keep it grounded:
 *
 * **Every turn recomputes the scenario server-side.** The endpoint takes the profile and the
 * portfolio and runs the policy engine again before any lens is asked anything. The conversation
 * cannot drift away from the household's actual numbers, because it is re-derived from them on
 * every message rather than carried along in the transcript.
 *
 * **Each conversation picks its own committee.** Asking about a concentrated position and asking
 * about an emergency fund are different consultations. A single global committee spanning both
 * would produce a transcript where half the participants had no business answering.
 */

import { useEffect, useRef, useState } from "react";
import type { AdvisorSummary, ChatTurn, DecisionCandidate } from "../types";
import type { Conversation } from "../lib/conversations";
import type { ProfileDraft } from "../lib/draft";
import { missingFields } from "../lib/draft";
import { navigate } from "../lib/router";
import { Card, EmptyState, InlineAlert, SectionHeader, StatusBadge } from "../ui";
import { ConnectKeyButton } from "../components/ConnectKeyButton";
import { CandidateStrip, LensCard } from "../components/CommitteeConsult";

export function ChatPage({
  conversations,
  activeId,
  advisors,
  profile,
  running,
  error,
  candidates,
  mockLLM,
  isConnected,
  onNewChat,
  onSelectChat,
  onDeleteChat,
  onToggleAdvisor,
  onAsk,
}: {
  conversations: Conversation[];
  activeId: string | null;
  advisors: AdvisorSummary[];
  profile: ProfileDraft;
  running: boolean;
  error: string | null;
  candidates: DecisionCandidate[];
  mockLLM: boolean;
  isConnected: boolean;
  onNewChat: () => void;
  onSelectChat: (id: string) => void;
  onDeleteChat: (id: string) => void;
  onToggleAdvisor: (conversationId: string, advisorId: string) => void;
  onAsk: (question: string) => void;
}) {
  const active = conversations.find((c) => c.id === activeId) ?? null;
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  const ready = missingFields(profile).length === 0;
  const canSend = ready && !running && draft.trim().length > 0 && (active?.advisorIds.length ?? 0) > 0;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [active?.turns.length, running]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSend) return;
    onAsk(draft.trim());
    setDraft("");
  }

  return (
    <div className="chat-layout">
      <aside className="chat-rail" aria-label="Consultations">
        <button className="primary full" onClick={onNewChat}>
          New consultation
        </button>

        <ul className="chat-list">
          {conversations.map((c) => (
            <li key={c.id}>
              <button
                className={`chat-list-item${c.id === activeId ? " active" : ""}`}
                onClick={() => onSelectChat(c.id)}
                aria-current={c.id === activeId ? "true" : undefined}
              >
                <span className="chat-list-title">{c.title}</span>
                <span className="tiny muted">
                  {c.turns.filter((t) => t.role === "user").length || "no"} question
                  {c.turns.filter((t) => t.role === "user").length === 1 ? "" : "s"} ·{" "}
                  {c.advisorIds.length} advisor{c.advisorIds.length === 1 ? "" : "s"}
                </span>
              </button>
              {conversations.length > 1 && (
                <button
                  className="linklike tiny chat-delete"
                  onClick={() => onDeleteChat(c.id)}
                  aria-label={`Delete ${c.title}`}
                >
                  Delete
                </button>
              )}
            </li>
          ))}
        </ul>
      </aside>

      <div className="chat-main">
        {!ready ? (
          <EmptyState title="Add your situation first">
            The committee reasons about your actual numbers, so there has to be a profile before
            there is anything to consult about.{" "}
            <button className="linklike" onClick={() => navigate("onboarding")}>
              Add your situation
            </button>
            .
          </EmptyState>
        ) : !active ? (
          <EmptyState title="No consultation open">
            Start one from the left.
          </EmptyState>
        ) : (
          <>
            <div className="chat-head">
              <div>
                <h1>{active.title}</h1>
                <p className="tiny muted" style={{ margin: "2px 0 0" }}>
                  Frameworks distilled from public writing, applied to your figures — not the
                  people themselves. Every question recomputes your scenario before anyone answers.
                </p>
              </div>
              {mockLLM ? (
                <StatusBadge tone="warn">Demo answers — no model called</StatusBadge>
              ) : (
                <StatusBadge tone="info">Bills your key</StatusBadge>
              )}
            </div>

            <AdvisorPicker
              advisors={advisors}
              selected={active.advisorIds}
              disabled={running}
              onToggle={(id) => onToggleAdvisor(active.id, id)}
            />

            {candidates.length > 0 && <CandidateStrip candidates={candidates} />}

            <div className="chat-thread">
              {active.turns.length === 0 && !running && (
                <Card tone="sunk">
                  <p className="small" style={{ margin: 0 }}>
                    Ask anything about this portfolio. The deterministic engine computes the
                    options first; the frameworks you selected then argue about those options
                    rather than inventing their own.
                  </p>
                  <div className="chat-suggestions">
                    {SUGGESTIONS.map((s) => (
                      <button key={s} className="choice" onClick={() => setDraft(s)}>
                        <span className="choice-label">{s}</span>
                      </button>
                    ))}
                  </div>
                </Card>
              )}

              {active.turns.map((turn, i) => (
                <TurnBlock key={i} turn={turn} />
              ))}

              {running && (
                <div className="chat-thinking" role="status">
                  Recomputing your scenario, then asking{" "}
                  {active.advisorIds.length === 1 ? "the framework" : "each framework"}…
                </div>
              )}
              <div ref={endRef} />
            </div>

            {error && (
              <InlineAlert tone="risk" title="That question did not go through">
                {error}
              </InlineAlert>
            )}

            {!isConnected && !mockLLM ? (
              <InlineAlert
                tone="info"
                title="Connect your Anthropic key to consult"
                action={<ConnectKeyButton label="Connect key" />}
              >
                Everything computed on the Portfolio and Decision pages cost nothing and needed no
                key. The argument about it runs on your account.
              </InlineAlert>
            ) : (
              <form className="chat-composer" onSubmit={submit}>
                <textarea
                  id="chat-input"
                  rows={2}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    // Enter sends, Shift+Enter breaks the line — the convention everyone
                    // already has in their fingers from every other chat.
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      submit(e as unknown as React.FormEvent);
                    }
                  }}
                  placeholder={
                    active.turns.length === 0
                      ? "Should I reduce NVDA to pay down my credit-card debt?"
                      : "Ask a follow-up…"
                  }
                  aria-label="Your question"
                />
                <div className="row-between" style={{ marginTop: 8 }}>
                  <span className="tiny muted">
                    {active.advisorIds.length === 0
                      ? "Select at least one framework above."
                      : "Enter to send · Shift+Enter for a new line"}
                  </span>
                  <button type="submit" className="primary" disabled={!canSend}>
                    {running ? "Asking…" : "Ask"}
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </div>
    </div>
  );
}

const SUGGESTIONS = [
  "Should I reduce NVDA to pay down my credit-card debt?",
  "Is this portfolio too concentrated for someone my age?",
  "What would you want to know that my numbers do not tell you?",
];

function AdvisorPicker({
  advisors,
  selected,
  disabled,
  onToggle,
}: {
  advisors: AdvisorSummary[];
  selected: string[];
  disabled: boolean;
  onToggle: (id: string) => void;
}) {
  return (
    <fieldset className="advisor-picker" disabled={disabled}>
      <legend className="metric-label">Who is in this consultation</legend>
      <div className="advisor-picker-row">
        {advisors.map((a) => {
          const on = selected.includes(a.advisor_id);
          return (
            <label key={a.advisor_id} className={`advisor-chip${on ? " selected" : ""}`}>
              <input
                type="checkbox"
                checked={on}
                onChange={() => onToggle(a.advisor_id)}
              />
              <span>{a.display_name}</span>
            </label>
          );
        })}
      </div>
      {selected.length === 0 && (
        <p className="tiny" style={{ color: "var(--oxblood)", margin: "6px 0 0" }}>
          Pick at least one — with nobody selected there is no one to ask.
        </p>
      )}
    </fieldset>
  );
}

function TurnBlock({ turn }: { turn: ChatTurn }) {
  if (turn.role === "user") {
    return (
      <div className="chat-user">
        <p className="chat-user-text">{turn.text}</p>
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
          <p key={c.label} className="small" style={{ margin: "3px 0 0" }}>
            <strong>{c.label}</strong> {c.from} → {c.to}
          </p>
        ))}
      </div>
    );
  }

  return (
    <div className="chat-answer">
      {turn.advisor_responses.map((r) => (
        <LensCard key={r.advisor_id} response={r} />
      ))}

      {turn.synthesis && (
        <Card tone="sunk">
          <SectionHeader
            title="Where that leaves it"
            action={
              turn.synthesis.unresolved_disagreement ? (
                <StatusBadge tone="warn">Unresolved</StatusBadge>
              ) : undefined
            }
          />
          <p className="small" style={{ margin: 0 }}>
            {turn.synthesis.headline}
          </p>
          {turn.synthesis.overrides.map((o) => (
            <p key={o} className="tiny muted" style={{ margin: "6px 0 0" }}>
              {o}
            </p>
          ))}
        </Card>
      )}
    </div>
  );
}
