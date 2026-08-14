import { useEffect, useState } from "react";
import { getQuotes } from "../api";
import type { Quote } from "../types";

/**
 * Delayed prices for the symbols currently in the portfolio. Costs nothing and needs no account
 * — the backend reads a free public feed and caches it, so this stays open to anonymous users
 * like the rest of the deterministic half.
 */
export function LivePrices({ symbols }: { symbols: string[] }) {
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [unpriced, setUnpriced] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keyed on the joined symbol list rather than the array: a new array with identical contents
  // is a new reference every render, which would refetch on every keystroke in the form.
  const key = symbols.join(",");

  useEffect(() => {
    if (!key) {
      setQuotes([]);
      setUnpriced([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getQuotes(key.split(","))
      .then((r) => {
        if (cancelled) return;
        setQuotes(r.quotes);
        setUnpriced(r.unpriced);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load prices.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  if (!key) return null;

  return (
    <section className="panel">
      <div className="row-between">
        <h2>Market prices</h2>
        <span className="badge free">delayed · no key used</span>
      </div>

      {loading && quotes.length === 0 && <p className="muted">Loading prices…</p>}
      {error && <p className="error">{error}</p>}

      {quotes.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="num">Price</th>
              <th className="num">Prev close</th>
              <th className="num">Change</th>
              <th>As of</th>
            </tr>
          </thead>
          <tbody>
            {quotes.map((q) => (
              <tr key={q.symbol}>
                <td>
                  <strong>{q.symbol}</strong>
                </td>
                <td className="num">${q.price.toFixed(2)}</td>
                <td className="num">
                  {q.previous_close === null ? "—" : `$${q.previous_close.toFixed(2)}`}
                </td>
                <td className={`num${q.change_pct !== null && q.change_pct < 0 ? " risk" : ""}`}>
                  {q.change_pct === null
                    ? "—"
                    : `${q.change_pct >= 0 ? "+" : ""}${(q.change_pct * 100).toFixed(2)}%`}
                </td>
                <td className="muted small">{new Date(q.as_of).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {unpriced.length > 0 && (
        <p className="muted small">
          No market price for {unpriced.join(", ")} — left out of the price history rather than
          guessed at.
        </p>
      )}

      <p className="fineprint">
        Exchange-delayed closing data, not a live tick feed, and not a brokerage connection. These
        prices do not change the market values you entered above; what they do feed is the
        volatility, drawdown, and correlation the committee sees when you run it.
      </p>
    </section>
  );
}
