/**
 * The application frame: a navigation rail, a topbar, and the workspace.
 *
 * The old masthead put a wordmark, five nav links, a connection pill and an account button in one
 * flex row and relied on 375px never happening. Here the rail owns navigation at every width —
 * it just becomes a drawer below 900px — so the topbar never holds more than a menu button, a
 * page title, and status.
 *
 * Navigation is ordered by the workflow rather than by the codebase. Methodology sits below a
 * rule with the secondary items, because "how it works" competing for attention with "your
 * portfolio" is a research demo's priority, not a product's.
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { AccountControl } from "../components/AccountControl";
import { useAnthropicConnection } from "../context/AnthropicConnectionContext";
import type { Route } from "../lib/router";
import { navigate } from "../lib/router";
import { StatusBadge } from "../ui";

const PRIMARY: { id: Route; label: string }[] = [
  { id: "decision", label: "Decision" },
  { id: "portfolio", label: "Portfolio" },
  { id: "investors", label: "Investor library" },
  { id: "reports", label: "Reports" },
];

const SECONDARY: { id: Route; label: string }[] = [
  { id: "settings", label: "Settings" },
  { id: "methodology", label: "Methodology" },
];

const TITLES: Record<Route, string> = {
  welcome: "Welcome",
  onboarding: "Set up",
  decision: "Decision",
  portfolio: "Portfolio",
  investors: "Investor library",
  reports: "Reports",
  settings: "Settings",
  methodology: "Methodology",
};

export function AppShell({
  route,
  children,
  demo,
  onExitDemo,
  reportCount,
}: {
  route: Route;
  children: ReactNode;
  demo?: boolean;
  onExitDemo?: () => void;
  reportCount?: number;
}) {
  const { isConnected } = useAnthropicConnection();
  const [drawer, setDrawer] = useState(false);

  // A drawer that survives navigation would cover the page the user just asked for.
  useEffect(() => setDrawer(false), [route]);

  function go(next: Route) {
    navigate(next);
  }

  return (
    <div className="shell">
      {drawer && (
        <button className="rail-scrim" aria-label="Close navigation" onClick={() => setDrawer(false)} />
      )}

      <nav className={`rail${drawer ? " open" : ""}`} aria-label="Main">
        <button className="rail-brand" onClick={() => go("decision")}>
          <p className="wordmark">
            AdvisorOS
            <span className="wordmark-sub">Investment decision intelligence</span>
          </p>
        </button>

        <div className="rail-nav">
          {PRIMARY.map((item) => (
            <button
              key={item.id}
              className={`navlink${route === item.id ? " active" : ""}`}
              aria-current={route === item.id ? "page" : undefined}
              onClick={() => go(item.id)}
            >
              {item.label}
              {item.id === "reports" && reportCount ? (
                <span className="navlink-badge">{reportCount}</span>
              ) : null}
            </button>
          ))}

          <p className="rail-section-label">More</p>
          {SECONDARY.map((item) => (
            <button
              key={item.id}
              className={`navlink${route === item.id ? " active" : ""}`}
              aria-current={route === item.id ? "page" : undefined}
              onClick={() => go(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="rail-foot">
          <span className={`conn-pill${isConnected ? " live" : ""}`}>
            <span className="conn-dot" />
            {isConnected ? "AI ready" : "AI not connected"}
          </span>
          <AccountControl />
        </div>
      </nav>

      <div className="workspace">
        <header className="topbar">
          <button
            className="rail-toggle"
            onClick={() => setDrawer((d) => !d)}
            aria-label="Open navigation"
            aria-expanded={drawer}
          >
            ☰
          </button>
          <span className="mobile-brand">AdvisorOS</span>
          <h1 className="topbar-title topbar-desktop-only">{TITLES[route]}</h1>
          <div className="topbar-spacer" />
          <span className="topbar-desktop-only">
            <StatusBadge tone={isConnected ? "good" : "neutral"}>
              {isConnected ? "AI ready" : "AI not connected"}
            </StatusBadge>
          </span>
        </header>

        {demo && (
          <div className="demo-banner">
            <strong>Sample household</strong>
            <span>
              These figures are a demonstration, not your data. Nothing here is saved to your
              account.
            </span>
            {onExitDemo && (
              <button className="linklike tap" onClick={onExitDemo}>
                Use my own data instead
              </button>
            )}
          </div>
        )}

        <main className="page">{children}</main>
      </div>
    </div>
  );
}
