/**
 * How one lens's answer and the option set are drawn.
 *
 * Split out when the consultation moved beside the holdings table: the wrapper component that
 * used to own these went with the Decision page, and a file named for a component it no longer
 * contains is a file nobody can find.
 */

import type { AdvisorConsultResponse, DecisionCandidate } from "../types";
import { Card, StatusBadge } from "../ui";

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

