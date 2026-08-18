/**
 * Holdings as a portfolio table, not five unrelated form fields in a row.
 *
 * The editor is still the editor — manual entry, one row per position — but it now sits under a
 * real table with weights, a total, and an explicit statement of where each price came from. The
 * weight column is what makes concentration visible before the analysis says a word about it.
 *
 * The data-source column exists for a feature that does not ship yet. The brokerage connector is
 * built on the backend and not wired here, so every row today says "Entered manually" and the
 * Connect option says it is coming. That is a column with one value rather than a fabricated
 * status — when the connector lands it fills the same column instead of forcing a redesign.
 */

import type { HoldingDraft } from "../lib/draft";
import type { QuoteState } from "../lib/useQuotes";
import { humanAssetClass, money, percent } from "../lib/units";
import { Advanced, Card, EmptyState, InlineAlert, SectionHeader, StatusBadge } from "../ui";
import { HoldingsEditor } from "../components/HoldingsEditor";

export function PortfolioPage({
  holdings,
  quotes,
  demo,
  onHoldings,
}: {
  holdings: HoldingDraft[];
  quotes: QuoteState;
  demo: boolean;
  onHoldings: (h: HoldingDraft[]) => void;
}) {
  const total = holdings.reduce((sum, h) => sum + (h.market_value ?? 0), 0);
  const priced = holdings.filter((h) => quotes.quotes[h.symbol.trim().toUpperCase()]);
  const unpriced = holdings.filter(
    (h) => h.symbol.trim() && !quotes.quotes[h.symbol.trim().toUpperCase()],
  );

  return (
    <>
      <div className="page-head">
        <h1>Portfolio</h1>
        <p className="lede">
          What you hold, and what each position is worth. Weights are computed from market value,
          so a position that has grown into a concentration shows it here before any analysis runs.
        </p>
      </div>

      {demo && (
        <InlineAlert tone="warn" title="Sample holdings">
          These are demonstration figures. Editing them changes the demo only — nothing is saved
          to your account.
        </InlineAlert>
      )}

      <section>
        <SectionHeader
          title="Positions"
          action={
            <StatusBadge tone="neutral" title="Brokerage connection is not available yet.">
              Manual entry
            </StatusBadge>
          }
        />

        {holdings.length === 0 ? (
          <EmptyState title="No positions yet">
            Add what you hold below. The analysis works without a portfolio too — it just cannot
            say anything about concentration.
          </EmptyState>
        ) : (
          <Card tone="quiet">
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Asset class</th>
                    <th className="num">Shares</th>
                    <th className="num">Price</th>
                    <th className="num">Market value</th>
                    <th className="num">Weight</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings
                    .filter((h) => h.symbol.trim())
                    .map((h, i) => {
                      const symbol = h.symbol.trim().toUpperCase();
                      const quote = quotes.quotes[symbol];
                      const weight = total > 0 ? (h.market_value ?? 0) / total : 0;
                      return (
                        <tr key={`${symbol}-${i}`}>
                          <td>
                            <strong>{symbol}</strong>
                          </td>
                          <td className="muted">{humanAssetClass(h.asset_class)}</td>
                          <td className="num">{h.quantity?.toLocaleString() ?? "—"}</td>
                          <td className="num">{quote ? money(quote.price) : "—"}</td>
                          <td className="num">{money(h.market_value)}</td>
                          <td className="num">
                            <span className="weight-cell">
                              <span className={`weight-bar${weight > 0.25 ? " over" : ""}`}>
                                <span style={{ width: `${Math.min(100, weight * 100)}%` }} />
                              </span>
                              {percent(weight, 1)}
                            </span>
                          </td>
                          <td className="tiny muted">
                            {quote ? "Delayed quote" : "Entered manually"}
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={4}>Total</td>
                    <td className="num">{money(total)}</td>
                    <td className="num">100%</td>
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>

            {unpriced.length > 0 && (
              <p className="small muted" style={{ marginTop: 12 }}>
                No live quote for {unpriced.map((h) => h.symbol.toUpperCase()).join(", ")} — those
                market values are exactly what you entered, and are not being overwritten.
              </p>
            )}

            {priced.length > 0 && (
              <p className="tiny muted" style={{ marginTop: 8 }}>
                Quoted prices are exchange-delayed. Market value is shares × price where a quote
                exists; otherwise it is the value you typed.
              </p>
            )}
          </Card>
        )}
      </section>

      <section>
        <SectionHeader title="Edit positions" hint="Changes save automatically." />
        <Card>
          <HoldingsEditor holdings={holdings} quotes={quotes} onHoldings={onHoldings} />
        </Card>
      </section>

      <Advanced label="Where portfolio data can come from">
        <p>
          Positions are entered by hand today. A read-only brokerage connection is built on the
          server but is not enabled in this interface yet; when it is, connected positions will
          appear in the same table with their source named in the last column, and manual entry
          will keep working alongside it.
        </p>
        <p className="muted">
          AdvisorOS will never have trading permission on a connected account. Read-only is a
          product constraint, not a setting.
        </p>
      </Advanced>
    </>
  );
}
