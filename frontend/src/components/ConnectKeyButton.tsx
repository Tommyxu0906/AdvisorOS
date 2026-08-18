/**
 * "Connect key", available wherever a key is suddenly needed, without leaving the page.
 *
 * The old placement made the connection panel a permanent section of the Advisors page, so
 * everyone scrolled through a credentials form on every visit whether or not they were about to
 * run anything. Connection state belongs in Settings; the *prompt* to connect belongs exactly
 * where the user hits the wall, which is the run preflight and the distillation form.
 *
 * How keys work is a disclosure rather than a paragraph, because it is reassuring the first time
 * and noise every time after.
 */

import { useState } from "react";
import { ConnectPanel } from "./ConnectPanel";
import { Advanced, Overlay } from "../ui";

export function ConnectKeyButton({
  label = "Connect key",
  className = "primary",
}: {
  label?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>
        {label}
      </button>

      <Overlay
        open={open}
        onClose={() => setOpen(false)}
        title="Connect your Anthropic key"
        variant="modal"
      >
        <ConnectPanel />

        <Advanced label="How keys work here">
          <ul className="bullet-list" style={{ fontSize: 14 }}>
            <li>Held in this page's memory for the session only.</li>
            <li>
              Never written to browser storage — not localStorage, not sessionStorage, not a
              cookie. A refresh loses it, which is the intended tradeoff.
            </li>
            <li>Never persisted server-side, never written to a log line or a saved run.</li>
            <li>
              Passed per request, so inference bills your Anthropic account rather than whoever
              hosts this.
            </li>
          </ul>
        </Advanced>

        <div className="row-between" style={{ marginTop: 16 }}>
          <span className="small muted">You can disconnect at any time in Settings.</span>
          <button className="secondary" onClick={() => setOpen(false)}>
            Done
          </button>
        </div>
      </Overlay>
    </>
  );
}
