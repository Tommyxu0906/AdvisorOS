import type { ProfileDraft } from "../lib/draft";
import { missingFields } from "../lib/draft";
import { useAuth } from "../context/AuthContext";
import { SituationFields } from "./SituationFields";

/**
 * Asked once, before the app proper. Everything downstream — the need vector, the guardrails,
 * which advisors get selected — is computed from these numbers, so there is no useful version
 * of this product that runs without them.
 *
 * Shown to signed-out visitors too. Accounts decide whether the answers are *kept*, not whether
 * the analysis runs at all; gating intake on sign-in would quietly convert the free tier into a
 * login wall.
 */
export function IntakePage({
  profile,
  signedIn,
  onProfile,
  onDone,
}: {
  profile: ProfileDraft;
  signedIn: boolean;
  onProfile: (p: ProfileDraft) => void;
  onDone: () => void;
}) {
  const missing = missingFields(profile);

  return (
    <>
      <div className="page-head">
        <h1>Let's start with your situation</h1>
        <p className="lede">
          Nothing here is filled in for you, because a guessed figure produces a confident answer
          to the wrong question. This is asked once — afterwards it lives under{" "}
          <strong>Settings</strong>, and the home page is just your holdings and your question.
          None of it is sent to Claude until you run a committee.
        </p>
      </div>

      <section className="panel">
        <SituationFields profile={profile} onProfile={onProfile} />

        <div className="row-between" style={{ marginTop: "1.5rem" }}>
          <p className="muted small" style={{ margin: 0 }}>
            {missing.length > 0
              ? `Still needed: ${missing.join(", ")}.`
              : signedIn
                ? "Saved to your account as soon as you continue."
                : "Kept in this tab only — sign in later and it will be saved to your account."}
          </p>
          <button onClick={onDone} disabled={missing.length > 0}>
            Continue
          </button>
        </div>
      </section>
    </>
  );
}

/** The signed-out nudge, shown under the intake rather than in place of it. */
export function IntakeSignInNote() {
  const { isConfigured, user } = useAuth();
  if (!isConfigured || user) return null;
  return (
    <section className="panel">
      <p className="muted">
        You can use everything below without an account. Signing in only means you stop retyping
        this — your situation and your saved runs come back next time.
      </p>
    </section>
  );
}
