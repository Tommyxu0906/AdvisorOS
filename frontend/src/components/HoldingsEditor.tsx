import type { HoldingDraft } from "../lib/draft";
import { num, str } from "../lib/draft";
import type { QuoteState } from "../lib/useQuotes";
import { ASSET_CLASSES, RowEditor } from "./FormControls";

/**
 * The one thing that stays editable on the Analysis page. Positions change constantly — a
 * settings screen is the wrong place for something you adjust between two questions.
 */
export function HoldingsEditor({
  holdings,
  quotes,
  onHoldings,
}: {
  holdings: HoldingDraft[];
  quotes: QuoteState;
  onHoldings: (h: HoldingDraft[]) => void;
}) {
  return (
    <RowEditor
      title="Portfolio holdings"
      rows={holdings}
      onChange={onHoldings}
      blank={{ symbol: "", asset_class: "us_equity", quantity: null, market_value: null }}
      empty="No positions yet. Add a ticker and either a share count or what it is worth."
      render={(row, update) => {
        const quote = quotes.quotes[row.symbol.trim().toUpperCase()];
        return (
          <>
            <input
              placeholder="symbol"
              value={row.symbol}
              onChange={(e) => update({ ...row, symbol: e.target.value.toUpperCase() })}
            />
            <select
              value={row.asset_class}
              onChange={(e) => update({ ...row, asset_class: e.target.value })}
            >
              {ASSET_CLASSES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <input
              type="number"
              step="any"
              placeholder="shares"
              min={0}
              value={str(row.quantity)}
              onChange={(e) => {
                const quantity = num(e.target.value);
                // With a share count and a live price, the value follows the market instead of
                // whatever it was worth the day it was typed in.
                update({
                  ...row,
                  quantity,
                  market_value:
                    quantity !== null && quote
                      ? Number((quantity * quote.price).toFixed(2))
                      : row.market_value,
                });
              }}
            />
            <input
              type="number"
              placeholder="market value"
              min={0}
              value={str(row.market_value)}
              onChange={(e) => update({ ...row, market_value: num(e.target.value) })}
            />
            <PriceTag quote={quote} loading={quotes.loading} symbol={row.symbol} />
          </>
        );
      }}
    />
  );
}

function PriceTag({
  quote,
  loading,
  symbol,
}: {
  quote: { price: number; change_pct: number | null } | undefined;
  loading: boolean;
  symbol: string;
}) {
  if (!symbol.trim()) return <span className="muted small" />;
  if (!quote) return <span className="muted small">{loading ? "…" : "no price"}</span>;
  return (
    <span className={`small${quote.change_pct !== null && quote.change_pct < 0 ? " risk" : ""}`}>
      ${quote.price.toFixed(2)}
      {quote.change_pct !== null &&
        ` ${quote.change_pct >= 0 ? "+" : ""}${(quote.change_pct * 100).toFixed(2)}%`}
    </span>
  );
}
