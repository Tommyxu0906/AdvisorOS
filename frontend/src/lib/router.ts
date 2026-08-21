/**
 * Hash routing, in about forty lines, because the alternative was a dependency.
 *
 * Hash rather than history: the app deploys to static hosting, and `#/portfolio` needs no
 * rewrite rule to survive a refresh. The tradeoff — uglier URLs — is worth not having a
 * deploy-config bug that only shows up when someone shares a link.
 *
 * What this buys over the old `useState<View>` is the thing that was actually broken: refresh
 * and back. A workflow that spans several screens has to survive a reload, or the user who
 * refreshes on the report loses it.
 */

import { useEffect, useState } from "react";

export type Route =
  | "welcome"
  | "onboarding"
  | "decision"
  | "chat"
  | "portfolio"
  | "investors"
  | "reports"
  | "settings"
  | "methodology";

const ROUTES: Route[] = [
  "welcome",
  "onboarding",
  "decision",
  "chat",
  "portfolio",
  "investors",
  "reports",
  "settings",
  "methodology",
];

function parse(hash: string): Route {
  const cleaned = hash.replace(/^#\/?/, "").split("?")[0];
  return (ROUTES as string[]).includes(cleaned) ? (cleaned as Route) : "decision";
}

export function navigate(route: Route) {
  if (parse(window.location.hash) !== route) {
    window.location.hash = `#/${route}`;
  }
  // Deliberate: a route change is a new page as far as the reader is concerned, and inheriting
  // the previous page's scroll position is disorienting on a long report.
  window.scrollTo({ top: 0 });
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  return route;
}
