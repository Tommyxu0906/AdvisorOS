import { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { InlineAlert, Overlay } from "../ui";

/**
 * Sign up / log in, both email+password and Google, in one dialog.
 *
 * The password field only ever reaches supabase-js's signUp / signInWithPassword calls — see
 * AuthContext.tsx. Nothing in this component, or anywhere downstream of it, writes a password
 * to our own backend or database.
 *
 * Built on `Overlay` rather than the hand-rolled `.modal-panel` it used before. That was the
 * actual reason it looked out of place: the submit button and the mode tabs carried no class at
 * all, so they rendered as bare user-agent buttons in the middle of a designed page. Using the
 * shared primitive also means the dialog now traps focus and closes on Escape, which the
 * previous version did not.
 */
export function AuthModal({ onClose }: { onClose: () => void }) {
  const { error, clearError, signInWithGoogle, signUpWithPassword, signInWithPassword } =
    useAuth();

  const [mode, setMode] = useState<"signup" | "login">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [confirmSent, setConfirmSent] = useState(false);

  function switchMode(next: "signup" | "login") {
    setMode(next);
    clearError();
    setConfirmSent(false);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      if (mode === "signup") {
        const { needsEmailConfirm, error: submitError } = await signUpWithPassword(
          email,
          password,
        );
        if (submitError) return;
        if (needsEmailConfirm) {
          setConfirmSent(true);
        } else {
          onClose();
        }
      } else {
        const { error: submitError } = await signInWithPassword(email, password);
        if (!submitError) onClose();
      }
    } finally {
      setSubmitting(false);
    }
  }

  const submitLabel = submitting
    ? mode === "signup"
      ? "Creating account…"
      : "Logging in…"
    : mode === "signup"
      ? "Create account"
      : "Log in";

  return (
    <Overlay open onClose={onClose} title="Your account" variant="modal" size="narrow">
      {/* The segmented control is the only mode switch, so the dialog title stays neutral
          rather than repeating whichever tab is active. */}
      <div className="segmented" role="tablist" aria-label="Account mode">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "login"}
          className={mode === "login" ? "active" : ""}
          onClick={() => switchMode("login")}
        >
          Log in
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "signup"}
          className={mode === "signup" ? "active" : ""}
          onClick={() => switchMode("signup")}
        >
          Sign up
        </button>
      </div>

      {confirmSent ? (
        <InlineAlert tone="good" title="Check your inbox">
          We sent a confirmation link to <strong>{email}</strong>. Open it, then come back and log
          in.
        </InlineAlert>
      ) : (
        <form onSubmit={onSubmit} className="auth-form">
          <div className="field">
            <label htmlFor="auth-email">Email</label>
            <input
              id="auth-email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          <div className="field">
            <div className="label-row">
              <label htmlFor="auth-password">Password</label>
              {mode === "signup" && <span className="optional">6 characters minimum</span>}
            </div>
            <input
              id="auth-password"
              type="password"
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "auth-error" : undefined}
            />
          </div>

          {error && (
            <p className="field-error" id="auth-error" role="alert">
              {error}
            </p>
          )}

          <button type="submit" className="primary full" disabled={submitting}>
            {submitLabel}
          </button>
        </form>
      )}

      <div className="or-divider">or</div>

      <button className="secondary full" onClick={signInWithGoogle}>
        Continue with Google
      </button>

      <p className="fineprint auth-note">
        Your password is handled entirely by Supabase Auth and never reaches this application's
        server. Signing in unlocks saved run history — it never touches, or is touched by, your
        Anthropic API key.
      </p>
    </Overlay>
  );
}
