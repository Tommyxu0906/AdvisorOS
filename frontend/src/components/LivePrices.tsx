import type { QuoteState } from "../lib/useQuotes";

/**
 * The full price picture for the symbols in the portfolio. Costs nothing and needs no account —
 * the backend reads a free public feed and caches it, so this stays open to anonymous users like
 * the rest of the deterministic half. Prices come in as props rather than being fetched here, so
 * this panel and the holdings editor share one request.
 */
export function LivePrices({ state }: { state: QuoteState }) {
  const { quotes, unpriced, loading, error } = state;
  const rows = Object.values(quotes);

  if (rows.length === 0 && unpriced.length === 0 && !loading && !error) return null;

  return (
    <section className="panel">
      <div className="row-between">
        <h2>Market prices</h2>
        <span className="badge free">delayed · no key used</span>
      </div>

      {loading && rows.length === 0 && <p className="muted">Loading prices…</p>}
      {error && <p className="error">{error}</p>}

      {rows.length > 0 && (
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
            {rows.map((q) => (
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
        Exchange-delayed closing data, not a live tick feed, and not a brokerage connection. Enter
        a share count on a holding and its value is computed from the price below; leave it blank
        and the value stays exactly what you typed. Either way these prices feed the volatility,
        drawdown, and correlation the committee sees when you run it.
      </p>
    </section>
  );
}
