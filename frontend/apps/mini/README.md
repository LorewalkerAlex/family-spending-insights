# Native WeChat Mini Program

This directory is a native WeChat Mini Program project. Import **this directory itself** into WeChat Developer Tools:

```text
frontend/apps/mini
```

`project.config.json` points `miniprogramRoot` at `miniprogram/`, so no Taro/H5 build step is required.

## Local Backend

From the repository root, start the Canonical Backend on the default local port:

```powershell
$env:PYTHONPATH = "src"
uv run --frozen python -m family_spending serve
```

The development Mini Program uses `http://127.0.0.1:8765`. `project.config.json` disables request-domain validation for the local Developer Tools workflow only. Trial/release builds intentionally refuse to use the local origin until a production HTTPS API origin is configured in `miniprogram/config/runtime.ts`.

## Developer Tools

1. Open WeChat Developer Tools.
2. Import `frontend/apps/mini`.
3. Use the configured AppID in Developer Tools.
4. Compile. Home should show the latest visible month, monthly spending, income/net cash flow, pending Mapping Review count, and the five most recent transactions from the Canonical Backend.
5. Use the native tabs `首页 | 交易 | 记一笔 | 审核 | 更多`.
6. `交易` provides month selection, income/expense filtering, date grouping, and read-only transaction detail.
7. `记一笔` records Manual Source facts through the Canonical Manual Input API; it supports expense/income, amount, date, description reuse hints, optional note, and Backend-owned reconciliation results.
8. `审核` provides a lightweight Mapping Review flow: pending groups, representative transactions, Merchant/category selection, Backend Preview, explicit confirmation for a new Merchant, and Confirm Apply.
9. `更多` currently provides the persisted interface theme selector; Feedback and Scheduled Input remain later phases.

Developer Tools may create `project.private.config.json`; it is intentionally ignored by Git.

## Current status

The direct Developer Tools connectivity baseline was verified on 2026-08-16: the native Mini compiled in WeChat Developer Tools and successfully read real Canonical Backend data.

Phase 1 established the first product Home, native five-tab shell, shared WXSS foundation, and the persisted dopamine theme system (`酸柠 | 莓果 | 橘浪 | 葡萄`). Home reuses `/api/financial-summary`, `/api/transactions`, and `/api/mapping-reviews`; it does not duplicate reconciliation, mapping, enrichment, refund, or scheduling rules in the client.

Phase 2 adds the real transaction browser and read-only transaction detail. The transaction list reuses `/api/transactions`; detail reuses `/api/transactions/{transaction_id}`. Presentation-only formatting is shared between Home and Transactions so merchant/display fallback, amount direction, and date formatting stay consistent. The Mini keeps the full transaction query in page memory only and does not introduce a second persistent transaction cache or financial truth.

Phase 2 was manually verified in WeChat Developer Tools with real household data: month switching, all/expense/income filters, date grouping, income/refund amount direction, transaction detail navigation, and opening the same detail from Home all behaved as intended.

Phase 3 adds Manual Input through `/api/manual-descriptions` and `/api/manual-inputs`. The Mini validates the form for immediate feedback, offers lightweight reuse of known descriptions, and leaves final transaction identity/reconciliation to the Backend result (`created | matched | reused`). A successful mutation increments an in-memory refresh generation so Home and Transactions re-read authoritative Backend state the next time they become visible; no financial data is persisted in this client refresh helper.

Phase 3 UI and end-to-end creation were manually verified in WeChat Developer Tools. The temporary Manual Input used for that smoke was then deleted through the canonical Manual Input lifecycle and both its Manual Source and created Transaction were verified absent. Future mutation regression should use the repository's isolated temporary-household HTTP integration tests instead of asking UI verification to leave test facts in the real household.

Phase 4 adds the lightweight Mapping Review flow. The Review tab shows unmapped description groups and opens a detail page with representative transactions. Merchant/category input is followed by authoritative `/api/mapping-reviews/preview`; Apply reuses the preview token and requires explicit confirmation when the Backend reports a new Merchant. Successful Apply invalidates Home/Transactions/Review in memory so those pages re-read Backend state rather than keeping a second financial cache.

Phase 4 list/detail navigation and Preview were manually verified in WeChat Developer Tools without mutating the real Mapping catalog. Persistent Apply behavior, including stale-preview rejection and new-Merchant confirmation, is verified through automated tests and the isolated temporary-household HTTP integration path rather than by creating disposable real Mapping data.

Old Taro/H5 Mini screens are historical only and should not be restored as the design source.
