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
3. Use the shared `touristappid` baseline or select your own AppID in Developer Tools.
4. Compile. The home page should show Backend connection state and the latest visible financial summary.

Developer Tools may create `project.private.config.json`; it is intentionally ignored by Git.


## Current status

The direct Developer Tools baseline was verified on 2026-08-16: the native Mini compiled in WeChat Developer Tools and successfully read `/api/health` plus real Financial Summary data from the Canonical Backend.

The current home page is intentionally a connectivity verification surface, not the product UI baseline. Formal page/navigation/UI work starts from the native project and follows `docs/architecture/mini-product-ui-plan.md`. Old Taro/H5 Mini screens are historical only and should not be restored as the design source.
