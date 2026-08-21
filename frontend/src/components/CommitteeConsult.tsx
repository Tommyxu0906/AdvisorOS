/**
 * The investment committee: lenses arguing about a scenario the engine already computed.
 *
 * Not a chat app. There is no bubble alignment, no avatars, no typing indicator — this reads
 * like minutes of a consultation, because that is what it is. Each turn shows every lens's
 * position side by side rather than as a stream, so two frameworks disagreeing is visible at a
 * glance instead of requiring a scroll-back.
 *
 * Three things this component is careful about:
 *
 * **A stance is not prose.** The badge comes from the structured `stance` field, so "opposes"
 * is a fact about the response rather than a reading of its tone.
 *
 * **Overrides are shown.** When a lens preferred something the guardrails forbid, the
 * correction is rendered under its answer. Hiding it would produce a transcript in which every
 * framework conveniently agrees with the computation.
 *
 * **A parse failure is not an abstention.** A lens whose output could not be read contributed
 * nothing, and saying "abstained" would credit it with a considered decision it never made.
 */

import { useState } from "react";
import type {
  AdvisorConsultResponse,
  ChatTurn,
  ConsultSynthesis,
  DecisionCandidate,
} from "../types";
import { Card, InlineAlert, SectionHeader, StatusBadge } from "../ui";
import { ConnectKeyButton } from "./ConnectKeyButton";

const STANCE_LABEL: Record<string, string> = {
  endorse: "Endorses",
  oppose: "Opposes",
  mixed: "Partly",
  abstain: "No view",
};

const STANCE_TONE: Record<string, "good" | "risk" | "warn" | "neutral"> = {
  endorse: "good",
  oppose: "risk",
  mixed: "warn",
  abstain: "neutral",
};

export function CommitteeConsult({
  turns,
  candidates,
  running,
  error,
  isConnected,
  advisorNames,
  onAsk,
  mockLLM = false,
}: {
  turns: ChatTurn[];
  candidates: DecisionCandidate[];
  running: boolean;
  error: string | null;
  isConnected: boolean;
  advisorNames: string[];
  onAsk: (question: string) => void;
  /** Server is serving canned answers. Stated on screen — never passed off as a real run. */
  mockLLM?: boolean;
}) {
  const [draft, setDraft] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const question = draft.trim();
    if (!question || running) return;
    onAsk(question);
    setDraft("");
  }

  return (
    <section>
      <SectionHeader
        title="Investment committee"
        hint={
          advisorNames.length
            ? `${advisorNames.join(" and ")} — reasoning frameworks applied to the scenario above, not the people themselves.`
            : "Reasoning frameworks applied to the scenario above."
        }
        action={
          mockLLM ? (
            <StatusBadge tone="warn">Demo answers — no model called</StatusBadge>
          ) : (
            <StatusBadge tone="info">Bills your key</StatusBadge>
          )
        }
      />

      {candidates.length > 0 && <CandidateStrip candidates={candidates} />}

      {turns.length === 0 && !running && (
        <Card tone="sunk">
          <p className="small muted" style={{ margin: 0 }}>
            Ask the committee about the scenario above. Each framework will say whether it backs
            the computed steps, what it would do instead, and what the computation cannot see.
          </p>
        </Card>
      )}

      <div className="consult-thread">
        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <div key={`u${i}`} className="consult-question">
              <p className="metric-label">You asked</p>
              <p className="consult-question-text">{turn.text}</p>
            </div>
          ) : turn.assumption ? (
            <div key={`a${i}`} className="assumption-applied">
              <p className="metric-label" style={{ margin: 0 }}>
                Assumption applied — scenario recomputed
              </p>
              {turn.assumption.map((c) => (
                <p key={c.label} className="small" style={{ margin: "3px 0 0" }}>
                  <strong>{c.label}</strong> {c.from} → {c.to}
                </p>
              ))}
              <p className="tiny muted" style={{ margin: "6px 0 0" }}>
                The figures above changed and the engine recomputed before anyone was asked
                again. Ask a follow-up to see how the frameworks read the new scenario.
              </p>
            </div>
          ) : (
            <div key={`c${i}`} className="consult-round">
              <div className="consult-lenses">
                {turn.advisor_responses.map((r) => (
                  <LensCard key={r.advisor_id} response={r} />
                ))}
              </div>
              {turn.synthesis && <Synthesis synthesis={turn.synthesis} />}
            </div>
          ),
        )}
      </div>

      {running && (
        <Card tone="sunk">
          <p className="small muted" style={{ margin: 0 }}>
            Consulting {advisorNames.join(" and ")}…
          </p>
        </Card>
      )}

      {error && (
        <InlineAlert tone="risk" title="The consultation failed">
          {error}
        </InlineAlert>
      )}

      {!isConnected && !mockLLM ? (
        <InlineAlert
          tone="info"
          title="Consulting the committee needs your Anthropic key"
          action={<ConnectKeyButton />}
        >
          Everything above this point was computed without one and cost nothing.
        </InlineAlert>
      ) : (
        <form className="consult-composer" onSubmit={submit}>
          {mockLLM && (
            <InlineAlert tone="warn" title="Demo mode: these answers are canned">
              This server was started with AIFA_MOCK_LLM=1, so no model is called and no key is
              spent. The stances are fixed placeholders that exist to exercise the interface —
              they are not analysis, and the disagreement below was written into the mock.
            </InlineAlert>
          )}
          <label htmlFor="consult-input" className="metric-label">
            {turns.length === 0 ? "Ask the committee" : "Follow up"}
          </label>
          <div className="consult-input-row">
            <input
              id="consult-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Do I really need to reduce NVDA?"
              disabled={running}
            />
            <button type="submit" className="primary" disabled={running || !draft.trim()}>
              {running ? "Asking…" : "Ask"}
            </button>
          </div>
          <p className="tiny muted" style={{ marginTop: 6 }}>
            Asking a question never changes your figures. To test a different assumption, edit it
            above and apply it — the scenario recomputes first, then the committee reacts to it.
          </p>
        </form>
      )}
    </section>
  );
}

/** The choice set, so a reader can see what is actually on the table — including what is not. */
export function CandidateStrip({ candidates }: { candidates: DecisionCandidate[] }) {
  return (
    <Card tone="quiet">
      <p className="metric-label" style={{ marginBottom: 10 }}>
        What is on the table
      </p>
      <ul className="candidate-list">
        {candidates.map((c) => (
          <li key={c.candidate_id} className={c.feasible ? "" : "candidate-blocked"}>
            <div className="row-between" style={{ gap: 10 }}>
              <strong className="small">{c.label}</strong>
              {c.feasible ? (
                <StatusBadge tone="neutral">Available</StatusBadge>
              ) : (
                <StatusBadge tone="risk">Ruled out</StatusBadge>
              )}
            </div>
            <p className="tiny muted" style={{ margin: "4px 0 0" }}>
              {c.summary}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function LensCard({ response }: { response: AdvisorConsultResponse }) {
  if (response.parse_failed) {
    return (
      <Card as="article" tone="sunk">
        <div className="row-between" style={{ marginBottom: 6 }}>
          <h3 className="lens-name">{response.display_name} lens</h3>
          <StatusBadge tone="warn">No answer</StatusBadge>
        </div>
        <p className="small muted" style={{ margin: 0 }}>
          This framework's response could not be read, so it contributed nothing to the outcome.
          That is not the same as it having no view.
        </p>
      </Card>
    );
  }

  return (
    <Card as="article" tone="quiet">
      <div className="row-between" style={{ marginBottom: 6, gap: 8 }}>
        <h3 className="lens-name">{response.display_name} lens</h3>
        <span className="lens-badges">
          <StatusBadge tone={STANCE_TONE[response.stance] ?? "neutral"}>
            {STANCE_LABEL[response.stance] ?? response.stance}
          </StatusBadge>
          <StatusBadge tone="neutral" title="Uncalibrated — shown as a band, never a probability.">
            {response.confidence_signal} confidence
          </StatusBadge>
        </span>
      </div>

      {response.declined ? (
        <p className="small" style={{ marginTop: 0 }}>
          {response.declined_reason ||
            "This framework declined to opine, which its declared boundaries permit."}
        </p>
      ) : (
        <p className="lens-rationale">{response.rationale}</p>
      )}

      {response.risks_or_missing_information.length > 0 && (
        <>
          <p className="metric-label" style={{ marginTop: 12 }}>
            What the computation cannot see
          </p>
          <ul className="bullet-list" style={{ fontSize: 13.5, marginBottom: 0 }}>
            {response.risks_or_missing_information.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
        </>
      )}

      {response.corrections.length > 0 && (
        <div className="lens-correction">
          <p className="metric-label" style={{ marginBottom: 4 }}>
            Overruled by the constraints
          </p>
          {response.corrections.map((c) => (
            <p key={c} className="tiny" style={{ margin: 0 }}>
              {c}
            </p>
          ))}
        </div>
      )}
    </Card>
  );
}

function Synthesis({ synthesis }: { synthesis: ConsultSynthesis }) {
  return (
    <Card tone="sunk">
      <div className="row-between" style={{ marginBottom: 6, gap: 8 }}>
        <p className="metric-label" style={{ margin: 0 }}>
          Where that leaves it
        </p>
        {synthesis.unresolved_disagreement && (
          <StatusBadge tone="warn">Unresolved disagreement</StatusBadge>
        )}
      </div>
      <p className="summary" style={{ margin: 0 }}>
        {synthesis.headline}
      </p>

      {(synthesis.endorsing.length > 0 || synthesis.opposing.length > 0) && (
        <p className="tiny muted" style={{ marginTop: 8, marginBottom: 0 }}>
          {synthesis.endorsing.length > 0 && <>For: {synthesis.endorsing.join(", ")}. </>}
          {synthesis.opposing.length > 0 && <>Against: {synthesis.opposing.join(", ")}. </>}
          {synthesis.abstaining.length > 0 && <>No view: {synthesis.abstaining.join(", ")}.</>}
        </p>
      )}

      {synthesis.overrides.length > 0 && (
        <p className="tiny muted" style={{ marginTop: 8, marginBottom: 0 }}>
          A preference was overruled by a computed constraint — see the lens it belongs to. The
          selection is chosen by code from what remains feasible, not by any framework.
        </p>
      )}
    </Card>
  );
}
