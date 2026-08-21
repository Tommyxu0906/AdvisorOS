/**
 * Saved consultations: when, who was in the room, and what it concluded.
 *
 * This is the history the chat panel deliberately does not keep. A conversation is written after
 * every completed round rather than at some end the user never signals, so a transcript that was
 * abandoned mid-thought is still here — which is usually the one worth rereading.
 *
 * Three columns because those are the three questions someone actually asks of their own
 * history: when was this, who did I ask, and what did it come to. The transcript itself is a
 * disclosure rather than the default view; a page that opened six full conversations at once
 * would be unreadable.
 */

import { useEffect, useState } from "react";
import { getConsultation, listConsultations } from "../api";
import { useAuth } from "../context/AuthContext";
import type { ConsultationDetail, ConsultationSummary } from "../types";
import { Advanced, Card, EmptyState, InlineAlert, StatusBadge } from "../ui";
import { LensCard } from "./LensCard";

export function ConsultationHistory() {
  const { session } = useAuth();
  const accessToken = session?.access_token ?? null;
  const [items, setItems] = useState<ConsultationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken) return;
    listConsultations(accessToken)
      .then(setItems)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load your history."));
  }, [accessToken]);

  if (error) {
    return (
      <InlineAlert tone="warn" title="History is unavailable">
        {error}
      </InlineAlert>
    );
  }

  if (items === null) {
    return <p className="small muted">Loading…</p>;
  }

  if (items.length === 0) {
    return (
      <EmptyState title="No consultations yet">
        Ask the committee anything on the Portfolio page and it will be saved here — with the
        time, who answered, and what it concluded.
      </EmptyState>
    );
  }

  return (
    <div className="consult-history">
      {items.map((item) => (
        <ConsultationRow key={item.conversation_id} item={item} accessToken={accessToken!} />
      ))}
    </div>
  );
}

function ConsultationRow({
  item,
  accessToken,
}: {
  item: ConsultationSummary;
  accessToken: string;
}) {
  const [detail, setDetail] = useState<ConsultationDetail | null>(null);
  const [loading, setLoading] = useState(false);

  async function open() {
    if (detail || loading) return;
    setLoading(true);
    try {
      setDetail(await getConsultation(accessToken, item.conversation_id));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card tone="quiet">
      <div className="row-between" style={{ gap: 12, alignItems: "start" }}>
        <div style={{ minWidth: 0 }}>
          <h3 className="consult-title">{item.title}</h3>
          <p className="tiny muted" style={{ margin: "3px 0 0" }}>
            {formatWhen(item.updated_at)} · {item.question_count} question
            {item.question_count === 1 ? "" : "s"} · {item.depth}
          </p>
        </div>
        {item.unresolved ? (
          <StatusBadge tone="warn">Unresolved</StatusBadge>
        ) : (
          <StatusBadge tone="neutral">{item.advisor_ids.length} advisors</StatusBadge>
        )}
      </div>

      <p className="metric-label" style={{ margin: "12px 0 4px" }}>
        Who was asked
      </p>
      <div className="focus-chips">
        {item.advisor_ids.map((id) => (
          <StatusBadge key={id} tone="neutral">
            {id.replace(/_/g, " ")}
          </StatusBadge>
        ))}
      </div>

      {item.conclusion && (
        <>
          <p className="metric-label" style={{ margin: "12px 0 4px" }}>
            Where it landed
          </p>
          <p className="small" style={{ margin: 0, maxWidth: "68ch" }}>
            {item.conclusion}
          </p>
        </>
      )}

      <div onClick={open} onKeyDown={(e) => e.key === "Enter" && open()}>
        <Advanced label="Read the transcript">
          {loading && <p className="small muted">Loading…</p>}
          {detail && (
            <div className="consult-transcript">
              {detail.turns.map((turn, i) =>
                turn.role === "user" ? (
                  <div key={i} className="pchat-user">
                    <p>{String(turn.text)}</p>
                  </div>
                ) : (
                  <div key={i} className="pchat-answer">
                    {(turn.advisor_responses ?? []).map((r, j: number) => (
                      <LensCard key={j} response={r} />
                    ))}
                  </div>
                ),
              )}
            </div>
          )}
        </Advanced>
      </div>
    </Card>
  );
}

/**
 * Absolute date, relative only for today.
 *
 * "3 days ago" is friendlier and worse: someone rereading their own history is usually trying to
 * place it against something else that happened, and a date does that where an interval does not.
 */
function formatWhen(iso: string): string {
  const when = new Date(iso);
  const today = new Date();
  const sameDay = when.toDateString() === today.toDateString();
  const time = when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (sameDay) return `Today at ${time}`;
  return `${when.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })} at ${time}`;
}
