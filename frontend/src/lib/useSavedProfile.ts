import { useEffect, useRef, useState } from "react";
import { getSavedProfile, saveProfile } from "../api";
import { useAuth } from "../context/AuthContext";
import type { HoldingDraft, ProfileDraft } from "./draft";
import { fromPortfolioInput, fromProfileInput, toPortfolioInput, toProfileInput } from "./draft";

export type SaveStatus = "anonymous" | "loading" | "saved" | "saving" | "error";

/**
 * Keeps the form in sync with the account's stored profile.
 *
 * Signed out, this does nothing at all — the whole deterministic half still works, the numbers
 * just live in the tab and disappear with it. Signing in is what makes them persist.
 *
 * Only a *complete* profile is saved. A half-filled form is not a state worth storing, and
 * writing partial rows would mean the load path had to reason about profiles that cannot be
 * analyzed.
 */
export function useSavedProfile(
  profile: ProfileDraft,
  holdings: HoldingDraft[],
  onLoad: (profile: ProfileDraft, holdings: HoldingDraft[]) => void,
): { status: SaveStatus; error: string | null } {
  const { session } = useAuth();
  const accessToken = session?.access_token ?? null;

  const [status, setStatus] = useState<SaveStatus>("anonymous");
  const [error, setError] = useState<string | null>(null);

  // Until the stored profile has arrived, the form still holds whatever was typed while signed
  // out. Saving in that window would overwrite the account's real data with a blank form.
  const loaded = useRef(false);
  // What was last written, so an unchanged form doesn't re-PUT on every keystroke.
  const lastSaved = useRef<string | null>(null);

  /**
   * The exact bytes a save would send. Both the load and save paths derive their baseline
   * through this, so a freshly loaded profile compares equal to itself — comparing against the
   * server's own response instead would differ in shape (it carries currency and price_series
   * that the draft never collects) and fire a pointless write immediately after every login.
   */
  function payloadFor(p: ProfileDraft, h: HoldingDraft[]): string | null {
    const profileInput = toProfileInput(p);
    if (!profileInput) return null;
    const portfolioInput = toPortfolioInput(h);
    return JSON.stringify({
      profile: profileInput,
      portfolio: portfolioInput.holdings.length ? portfolioInput : null,
    });
  }

  useEffect(() => {
    if (!accessToken) {
      loaded.current = false;
      lastSaved.current = null;
      setStatus("anonymous");
      return;
    }
    let cancelled = false;
    setStatus("loading");
    setError(null);

    getSavedProfile(accessToken)
      .then((saved) => {
        if (cancelled) return;
        if (saved.profile) {
          const loadedProfile = fromProfileInput(saved.profile);
          const loadedHoldings = fromPortfolioInput(saved.portfolio);
          onLoad(loadedProfile, loadedHoldings);
          lastSaved.current = payloadFor(loadedProfile, loadedHoldings);
        }
        loaded.current = true;
        setStatus("saved");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Could not load your saved profile.");
        setStatus("error");
      });

    return () => {
      cancelled = true;
    };
    // onLoad is a setState pair from the caller and is stable enough in practice; adding it
    // would refetch the stored profile on every render of App.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  useEffect(() => {
    if (!accessToken || !loaded.current) return;

    const payload = payloadFor(profile, holdings);
    if (payload === null || payload === lastSaved.current) return;

    const profileInput = toProfileInput(profile)!;
    const portfolioInput = toPortfolioInput(holdings);

    const timer = setTimeout(() => {
      setStatus("saving");
      saveProfile(
        accessToken,
        profileInput,
        portfolioInput.holdings.length ? portfolioInput : null,
      )
        .then(() => {
          lastSaved.current = payload;
          setStatus("saved");
          setError(null);
        })
        .catch((e) => {
          setError(e instanceof Error ? e.message : "Could not save your profile.");
          setStatus("error");
        });
    }, 1200);

    return () => clearTimeout(timer);
  }, [accessToken, profile, holdings]);

  return { status, error };
}
