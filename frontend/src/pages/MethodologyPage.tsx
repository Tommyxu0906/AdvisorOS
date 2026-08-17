/**
 * Where the implementation detail lives, on purpose.
 *
 * The old copy scattered "seven-dimension read", "expertise vector" and "schema-constrained JSON"
 * across the primary product surfaces, where they described how the thing is built to someone
 * trying to work out what it does. Here that vocabulary is correct and welcome — this page exists
 * for the reader who wants to know whether the engineering is real.
 */

import { Advanced, Card, SectionHeader } from "../ui";

export function MethodologyPage() {
  return (
    <>
      <div className="page-head">
        <h1>Methodology</h1>
        <p className="lede">
          Three ideas govern the design: the model proposes and reasons, code calculates and
          decides, and you own the credentials and the cost.
        </p>
      </div>

      <Card>
        <SectionHeader title="Code decides, the model reasons" />
        <p className="prose">
          Most of this product runs without an API call. Savings rate, emergency-fund coverage,
          debt ratios, portfolio concentration, and a seven-dimension read of where a profile most
          needs help are computed in plain Python on the AdvisorOS server. So is advisor routing:
          each persona carries a scored expertise vector, and the selector picks the smallest team
          that covers the gaps. You can change your situation all day and watch the analysis update
          without spending a cent.
        </p>
        <p className="prose">
          Claude is called for exactly four things — reading intent out of your question, advisor
          analysis, cross-examination, and final synthesis. Financial guardrails are never left to
          the model: blocking conditions such as a thin emergency reserve or high-APR debt are
          computed in code, injected into the prompt as hard constraints, and the final report is
          re-validated against them afterwards.
        </p>
        <Advanced label="Why the interface says 'deterministic' and not 'local'">
          <p>
            The free analysis runs on the AdvisorOS server, not in your browser. It involves no
            model call and costs nothing, but it is a network request, so calling it "local" would
            be inaccurate. The distinction matters for anyone reasoning about where their figures
            travel, which is exactly the sort of person this page is for.
          </p>
        </Advanced>
      </Card>

      <Card>
        <SectionHeader title="What distillation actually does" />
        <p className="prose">
          An advisor is not a prompt saying "answer like Warren Buffett". It is produced by a
          multi-stage pipeline that runs <em>once</em>: a planner proposes research questions aimed
          at how the subject makes decisions and where they fail, several research passes run
          concurrently against those questions, and a synthesis pass compresses the findings into a
          single structured profile. Every stage returns schema-constrained JSON, never free text.
        </p>
        <p className="prose">
          What comes out is a typed artifact rather than a personality: a scored expertise vector
          the router consumes directly, plus mental models, heuristics, reasoning rules, declared
          blind spots, and the questions the persona will decline to answer. Validation code rejects
          the result outright if it scores zero everywhere — that persona could never be selected —
          or if it declares no blind spots, on the view that a persona claiming no limits has no
          business in front of someone's finances.
        </p>
        <p className="prose">
          The full manifest is kept for provenance, but committee runs send only a compressed
          profile of roughly 1,200 tokens. That split is the point: distillation is expensive and
          happens once, while reuse is cheap and happens on every question.
        </p>
        <p className="prose">
          What distillation does <em>not</em> do is verify a track record. It reads public writing
          and available evidence. A lens that reproduces how someone argued is not evidence that
          their decisions were good, and the interface does not claim otherwise.
        </p>
      </Card>

      <Card>
        <SectionHeader title="On confidence, and why it is shown as a band" />
        <p className="prose">
          Each advisor reports a confidence between 0 and 1. It is the model's own sense of how
          strongly it holds a view, and it has not been calibrated against outcomes — so it is not
          a probability, and the interface will not render it as one.
        </p>
        <p className="prose">
          This is not caution for its own sake. On a comparable behavioural prediction task built
          from institutional filings, predictions made with 0.6–0.7 stated confidence turned out
          correct 42.9% of the time. A number that is wrong by roughly seventeen points of
          probability should not appear to two decimal places beside a financial recommendation.
          Confidence is therefore displayed as High, Medium or Low, marked uncalibrated, with the
          raw value available under Technical details in any report.
        </p>
      </Card>

      <Card>
        <SectionHeader title="Why you bring the key" />
        <p className="prose">
          Inference is billed to your Anthropic account, not to whoever hosts this. The server holds
          no API key of its own and has no fallback credential to charge — a committee run without
          a key is unavailable rather than quietly billed elsewhere. Your key lives in this page's
          memory for the session, is passed per request, and is never written to browser storage, a
          database, a log line, or a saved run.
        </p>
        <p className="prose">
          Cost is shown rather than hidden. Before a run you get the stage count and an estimate;
          afterwards you get actual token counts with per-stage and per-advisor attribution. The
          three depth modes exist to make the tradeoff explicit.
        </p>
      </Card>

      <Card tone="sunk">
        <SectionHeader title="What this is not" />
        <ul className="bullet-list">
          <li>Not a licensed advisor, and not personalized investment advice.</li>
          <li>Not connected to any brokerage. It cannot place a trade or move money.</li>
          <li>Not a forecast. Nothing here predicts a price or claims to beat a market.</li>
          <li>
            Not an optimizer. The analysis applies transparent rules to your figures; it does not
            solve for an optimal portfolio, because the inputs that would require are estimates
            nobody can supply honestly.
          </li>
        </ul>
      </Card>
    </>
  );
}
