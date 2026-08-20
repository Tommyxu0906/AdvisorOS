/**
 * The primitives, in one file because they are small and always imported together.
 *
 * Each of these exists because at least two real call sites needed it. Nothing here is
 * speculative — a `<Button>` wrapper, for instance, is absent, because the CSS classes already
 * do that job and wrapping them would only add a layer to read through.
 */

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";

// --- surfaces ------------------------------------------------------------------------------

export function Card({
  children,
  className = "",
  tone = "default",
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  tone?: "default" | "sunk" | "quiet" | "raised";
  as?: "section" | "div" | "article";
}) {
  return <Tag className={`card card-${tone} ${className}`}>{children}</Tag>;
}

export function SectionHeader({
  title,
  hint,
  action,
  level = 2,
}: {
  title: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
  level?: 2 | 3;
}) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <div className="section-header">
      <div>
        <Heading>{title}</Heading>
        {hint && <p className="section-hint">{hint}</p>}
      </div>
      {action && <div className="section-action">{action}</div>}
    </div>
  );
}

// --- data ----------------------------------------------------------------------------------

export function Metric({
  label,
  value,
  detail,
  tone = "default",
  size = "default",
}: {
  label: ReactNode;
  value: ReactNode;
  detail?: ReactNode;
  tone?: "default" | "risk" | "good" | "muted";
  size?: "default" | "large";
}) {
  return (
    <div className={`metric metric-${tone} metric-${size}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
      {detail && <span className="metric-detail">{detail}</span>}
    </div>
  );
}

/**
 * Status, always with a word.
 *
 * `tone` never carries the meaning on its own — a red dot and a green dot are the same dot to a
 * reader who cannot distinguish them, and this app uses colour for risk.
 */
export function StatusBadge({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "risk" | "warn" | "info";
  title?: string;
}) {
  return (
    <span className={`status-badge status-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function InlineAlert({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: "info" | "risk" | "warn" | "good";
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={`inline-alert alert-${tone}`} role={tone === "risk" ? "alert" : undefined}>
      <div className="alert-body">
        {title && <strong>{title}</strong>}
        {children && <div className="alert-text">{children}</div>}
      </div>
      {action && <div className="alert-action">{action}</div>}
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <p className="empty-title">{title}</p>
      {children && <p className="empty-text">{children}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton-line" />
      ))}
    </div>
  );
}

// --- disclosure ------------------------------------------------------------------------------

/**
 * The place engineering detail goes.
 *
 * Native `<details>` on purpose: it is keyboard-operable, findable by in-page search when open,
 * and needs no state. Token counts, raw scores and cost breakdowns all live behind one of these.
 */
export function Advanced({
  label = "Technical details",
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  return (
    <details className="advanced">
      <summary>{label}</summary>
      <div className="advanced-body">{children}</div>
    </details>
  );
}

// --- overlay ---------------------------------------------------------------------------------

/**
 * One overlay component for both modal and drawer, because they differ only in where they sit.
 *
 * Focus is moved in on open and returned on close, Escape dismisses, and the backdrop click
 * dismisses. Without the focus return, a keyboard user who closes a drawer lands back at the
 * top of the document instead of on the control they opened it with.
 *
 * Rendered through a portal to `document.body`, which is not a detail. `position: fixed` resolves
 * against the nearest *transformed* ancestor rather than the viewport, and the mobile navigation
 * rail is transformed — so an overlay opened from a control inside that rail (the account button
 * lives there) was clipped to the 300px drawer instead of covering the screen. The portal makes
 * an overlay independent of wherever it happens to be mounted.
 */
export function Overlay({
  open,
  onClose,
  title,
  children,
  variant = "modal",
  size = "default",
  labelledBy,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  variant?: "modal" | "drawer";
  /** `narrow` suits a single-column form; the default is sized for tables and rosters. */
  size?: "default" | "narrow";
  labelledBy?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreTo.current = document.activeElement as HTMLElement | null;
    panel.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key !== "Tab" || !panel.current) return;
      // Contain Tab inside the panel: an overlay a keyboard user can tab out of, into a page
      // they cannot see, is worse than no overlay.
      const focusable = panel.current.querySelectorAll<HTMLElement>(
        'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])',
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      restoreTo.current?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className={`overlay overlay-${variant} overlay-${size}`}>
      <button className="overlay-scrim" aria-label="Close" onClick={onClose} tabIndex={-1} />
      <div
        className="overlay-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy ?? "overlay-title"}
        ref={panel}
        tabIndex={-1}
      >
        <div className="overlay-head">
          <h2 id={labelledBy ?? "overlay-title"}>{title}</h2>
          <button className="icon-button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="overlay-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

// --- progress --------------------------------------------------------------------------------

export function Stepper({
  steps,
  current,
  onJump,
}: {
  steps: string[];
  current: number;
  onJump?: (index: number) => void;
}) {
  return (
    <ol className="stepper" aria-label="Setup progress">
      {steps.map((label, i) => {
        const state = i === current ? "current" : i < current ? "done" : "todo";
        // Only completed steps are reachable by clicking: jumping ahead would skip the fields
        // the later steps depend on.
        const reachable = onJump && i < current;
        return (
          <li key={label} className={`step step-${state}`} aria-current={i === current || undefined}>
            {reachable ? (
              <button className="step-button" onClick={() => onJump(i)}>
                <span className="step-index">{i + 1}</span>
                <span className="step-label">{label}</span>
              </button>
            ) : (
              <span className="step-button" aria-disabled={state === "todo"}>
                <span className="step-index">{i + 1}</span>
                <span className="step-label">{label}</span>
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
