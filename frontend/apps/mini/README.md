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
5. Use the native tabs `首页 | 交易 | 记一笔 | 审核 | 更多`; Phase 1 only implements Home, while the other tabs explicitly identify their later delivery phase.

Developer Tools may create `project.private.config.json`; it is intentionally ignored by Git.

## Current status

The direct Developer Tools connectivity baseline was verified on 2026-08-16: the native Mini compiled in WeChat Developer Tools and successfully read real Canonical Backend data.

Phase 1 replaces the old connectivity surface with the first product Home and establishes the native five-tab shell plus the small shared WXSS foundation described by `docs/architecture/mini-product-ui-plan.md`. The Home implementation reuses `/api/financial-summary`, `/api/transactions`, and `/api/mapping-reviews`; it does not duplicate reconciliation, mapping, enrichment, refund, or scheduling rules in the client.

Old Taro/H5 Mini screens are historical only and should not be restored as the design source.
