let financialDataVersion = 0;

/** Read the in-memory mutation generation; it is invalidation only, never financial data. */
export function currentFinancialDataVersion(): number {
  return financialDataVersion;
}

/** Mark canonical financial data stale after a successful mutation reaches the Backend. */
export function markFinancialDataChanged(): number {
  financialDataVersion += 1;
  return financialDataVersion;
}

/** Decide whether a page snapshot predates the latest successful local mutation. */
export function hasFinancialDataChanged(seenVersion: number): boolean {
  return seenVersion !== financialDataVersion;
}
