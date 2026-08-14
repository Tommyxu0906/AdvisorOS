import { useEffect, useState } from "react";
import { getQuotes } from "../api";
import type { Quote } from "../types";

export interface QuoteState {
  quotes: Record<string, Quote>;
  unpriced: string[];
  loading: boolean;
  error: string | null;
}

/**
 * Delayed prices for a set of symbols, fetched once and shared by every consumer.
 *
 * Lives in one hook rather than in each component that wants a price so that the holdings
 * editor and the price panel do not each fetch the same symbols on every render.
 */
export function useQuotes(symbols: string[]): QuoteState {
  // Keyed on the joined list rather than the array itself: a new array with identical contents
  // is a fresh reference every render, which would refetch on every keystroke in the form.
  const key = symbols.join(",");

  const [state, setState] = useState<QuoteState>({
    quotes: {},
    unpriced: [],
    loading: false,
    error: null,
  });

  useEffect(() => {
    if (!key) {
      setState({ quotes: {}, unpriced: [], loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));

    // Debounced: symbols change as the user types a ticker, and "N", "NV", "NVD" are all
    // requests for prices that do not exist.
    const timer = setTimeout(() => {
      getQuotes(key.split(","))
        .then((r) => {
          if (cancelled) return;
          setState({
            quotes: Object.fromEntries(r.quotes.map((q) => [q.symbol, q])),
            unpriced: r.unpriced,
            loading: false,
            error: null,
          });
        })
        .catch((e) => {
          if (cancelled) return;
          setState({
            quotes: {},
            unpriced: [],
            loading: false,
            error: e instanceof Error ? e.message : "Could not load prices.",
          });
        });
    }, 500);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key]);

  return state;
}
