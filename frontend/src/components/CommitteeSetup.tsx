/**
 * The proposed committee, inline, with customisation behind a drawer.
 *
 * Previously choosing a team meant leaving the analysis, visiting the Investor Library, reading a
 * roster table that included each persona's runtime token count, ticking boxes, and coming back.
 * The default path now needs none of that: the selector already picks a team from the computed
 * need vector, and this shows what it picked and why.
 *
 * `Customize` opens the roster in a drawer over the same page, so overriding the selection never
 * costs you your place in the flow.
 */

import { useState } from "react";
import type { AdvisorSummary, CommitteeSelection } from "../types";
import { Card, Overlay, SectionHeader, StatusBadge } from "../ui";

export function CommitteeSetup({
  selection,
  advisors,
  manualIds,
  onToggle,
  onReset,
}: {
  selection: CommitteeSelection;
  advisors: AdvisorSummary[];
  manualIds: Set<string> | null;
  onToggle: (id: string) => void;
  onReset: () => void;
}) {
  const [customizing, setCustomizing] = useState(false);

  // The selector already explains itself per advisor, so "why this one" is quoted from the
  // deterministic routing rather than written here. A hand-picked team has no such rationale,
  // and says so instead of borrowing one.
  const recommended = new Map(selection.selected.map((s) => [s.advisor_id, s]));
  const chosen = manualIds
    ? advisors.filter((a) => manualIds.has(a.advisor_id))
    : advisors.filter((a) => recommended.has(a.advisor_id));

  return (
    <section>
      <SectionHeader
        title="Proposed committee"
        hint={
          manualIds
            ? "You picked this team."
            : "Chosen from the priorities computed above — not from what you asked, alone."
        }
        action={
          <>
            {manualIds && (
              <button className="linklike small" onClick={onReset}>
                Use recommended
              </button>
            )}
            <button className="secondary" onClick={() => setCustomizing(true)}>
              Customize
            </button>
          </>
        }
      />

      <div className="grid-2">
        {chosen.map((advisor) => (
          <Card key={advisor.advisor_id} tone="sunk" as="article">
            <div className="row-between" style={{ marginBottom: 6 }}>
              <h3>{advisor.display_name}</h3>
              <EvidenceBadge advisor={advisor} />
            </div>
            <p className="small muted" style={{ marginBottom: 8 }}>
              {advisor.one_line}
            </p>
            {recommended.has(advisor.advisor_id) ? (
              <p className="tiny" style={{ margin: 0, color: "var(--green-deep)" }}>
                {recommended.get(advisor.advisor_id)!.rationale}
              </p>
            ) : (
              <p className="tiny muted" style={{ margin: 0 }}>
                Added by you — outside the recommended team.
              </p>
            )}
          </Card>
        ))}
      </div>

      <Overlay
        open={customizing}
        onClose={() => setCustomizing(false)}
        title="Choose your committee"
        variant="drawer"
      >
        <p className="small muted">
          Leave everything untouched to use the recommended team. Ticking any advisor switches this
          run to your selection.
        </p>
        <div className="stack" style={{ marginTop: 16 }}>
          {advisors.map((advisor) => {
            const active = manualIds
              ? manualIds.has(advisor.advisor_id)
              : recommended.has(advisor.advisor_id);
            return (
              <label
                key={advisor.advisor_id}
                className={`card card-sunk selectable${active ? " selected" : ""}`}
                style={{ padding: 14, display: "block", textTransform: "none", letterSpacing: 0 }}
              >
                <span className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
                  <input
                    type="checkbox"
                    checked={active}
                    onChange={() => onToggle(advisor.advisor_id)}
                    style={{ width: "auto", marginTop: 4 }}
                  />
                  <span style={{ minWidth: 0 }}>
                    <span className="row" style={{ gap: 8 }}>
                      <strong style={{ fontSize: 15 }}>{advisor.display_name}</strong>
                      <EvidenceBadge advisor={advisor} />
                    </span>
                    <span className="small muted" style={{ display: "block", marginTop: 4 }}>
                      {advisor.one_line}
                    </span>
                  </span>
                </span>
              </label>
            );
          })}
        </div>
        <div className="row-between" style={{ marginTop: 20 }}>
          <button className="linklike" onClick={onReset}>
            Reset to recommended
          </button>
          <button className="primary" onClick={() => setCustomizing(false)}>
            Done
          </button>
        </div>
      </Overlay>
    </section>
  );
}

/**
 * What the evidence behind a persona actually is.
 *
 * Only two states exist today, because only two are true. A behaviour-calibrated badge would be
 * the natural third, and the data to earn it does not exist yet — the Berkshire work is not wired
 * to these personas, so displaying it would be decoration claiming to be provenance.
 */
export function EvidenceBadge({ advisor }: { advisor: AdvisorSummary }) {
  if (advisor.origin === "builtin") {
    return (
      <StatusBadge tone="info" title="Hand-authored from the subject's public writing.">
        Public writings
      </StatusBadge>
    );
  }
  return (
    <StatusBadge tone="neutral" title="Produced by a Nuwa distillation run you paid for.">
      Distilled
    </StatusBadge>
  );
}
