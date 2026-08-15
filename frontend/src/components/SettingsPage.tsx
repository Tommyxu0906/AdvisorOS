import { useAuth } from "../context/AuthContext";
import type { ProfileDraft } from "../lib/draft";
import { missingFields } from "../lib/draft";
import type { SavedProfileState } from "../lib/useSavedProfile";
import { SituationFields } from "./SituationFields";

/** Where the situation collected at intake gets corrected when someone's life changes. */
export function SettingsPage({
  profile,
  saved,
  onProfile,
}: {
  profile: ProfileDraft;
  saved: SavedProfileState;
  onProfile: (p: ProfileDraft) => void;
}) {
  const { user } = useAuth();
  const missing = missingFields(profile);

  return (
    <>
      <div className="page-head">
        <h1>Settings</h1>
        <p className="lede">
          Your situation — income, expenses, debts, assets, and goals. Changing anything here
          re-runs the deterministic analysis and can change which advisors get selected. Your
          holdings are not here; those stay on the Analysis page, where they change most often.
        </p>
      </div>

      <section className="panel">
        <div className="row-between">
          <h2>Your situation</h2>
          <SaveBadge saved={saved} incomplete={missing.length > 0} />
        </div>

        {saved.error && <p className="error">{saved.error}</p>}
        {missing.length > 0 && (
          <p className="error">
            Incomplete, so nothing is being saved: {missing.join(", ")} still needed.
          </p>
        )}
        {!user && (
          <p className="muted">
            You are not signed in, so these numbers live in this tab only and will be gone when
            you close it.
          </p>
        )}

        <SituationFields profile={profile} onProfile={onProfile} />
      </section>
    </>
  );
}

export function SaveBadge({
  saved,
  incomplete,
}: {
  saved: SavedProfileState;
  incomplete: boolean;
}) {
  // "Saved" is claimed only after a write has actually come back. Every other state says what
  // is really true, including the one where an incomplete form means nothing is being stored.
  if (saved.status === "anonymous")
    return <span className="badge free">not saved — sign in to keep this</span>;
  if (saved.status === "loading") return <span className="badge free">loading…</span>;
  if (saved.status === "saving") return <span className="badge free">saving…</span>;
  if (saved.status === "error") return <span className="badge risk">not saved</span>;
  if (saved.status === "incomplete" || incomplete)
    return <span className="badge risk">not saved — incomplete</span>;
  return <span className="badge free">saved to your account</span>;
}
