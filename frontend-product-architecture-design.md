# Family Spending Insights Frontend Product Architecture

> Status: V1 design baseline before cross-platform frontend POC implementation
> Repository baseline: `LorewalkerAlex/family-spending-insights` `main` @ `f06e0dac733eb80804fb0342895a72a9c4ea0fd6`
> Scope: frontend product information architecture, Desktop/Mini presentation model, cross-platform boundaries, technology baseline, POC scope, and Design System V1.

## 1. Purpose

The existing `local_dashboard/` proved the backend/Application contracts and multiple product workflows, but it is still a development-oriented flat page. Financial Summary, Manual Input, Scheduled Input, Mapping Review, transaction management, and charts are all presented in one long document. The next frontend stage must replace that presentation model without changing the backend domain truth that has already been validated.

The target product has two formal presentation surfaces:

- **Desktop Web**: the primary large-screen personal finance workspace used in a desktop browser.
- **Mini**: the formal mobile presentation intended for the WeChat Mini Program.

Mini also has an **H5 Preview runtime** used only for development and testing from a desktop browser. H5 Preview is not a third product surface and is not intended to become a separately deployed mobile-web product.

The frontend architecture must preserve one product model while allowing Desktop and Mini to use interaction patterns appropriate to their platforms.

## 2. Product architecture principles

Four rules govern the frontend architecture:

1. **Backend owns business truth.** Refund reconciliation, net spending, month completeness, Mapping propagation, Review qualification, cash-flow calculations, and other financial facts remain backend responsibilities.
2. **Shared frontend owns product semantics.** API contracts, runtime decoding, shared view models, formatting, presentation transforms, and design tokens should be shared when they do not depend on a presentation runtime.
3. **Desktop and Mini own presentation.** Layout, navigation, page composition, dialogs/sheets, interaction density, mouse/keyboard behavior, touch behavior, and platform-specific components may differ.
4. **H5 is only a Mini development runtime.** The Mini H5 build exists to preview and test Mini presentation from a desktop browser; it does not create a separate mobile-web product requirement.

Do not optimize for the highest possible percentage of shared UI code. Prefer shared semantics and intentionally separate presentation over platform-condition branches scattered throughout the component tree.

## 3. Canonical information architecture

### 3.1 Canonical workspaces

The stable product workspaces are:

- **Overview** — read-first household financial overview and analytics.
- **Transactions** — household transaction explorer and transaction/source detail.
- **Review** — user decisions required by financial data quality or classification workflows.
- **Automation** — low-frequency management of Scheduled Inputs and future automation capabilities.
- **Feedback** — product feedback inbox used to capture and manage issues discovered during actual use.

### 3.2 Global commands

The stable global commands are:

- **Add Transaction** — create a Manual Source entry through the existing Manual Input/Application flow.
- **Send Feedback** — quickly capture a product problem or idea without leaving the current work context.

A global command is not automatically a workspace or navigation destination. For example, Add Transaction belongs to the Transactions capability but should not consume a permanent navigation slot.

### 3.3 Capability-to-workspace mapping

Current implementation concepts map to the new product structure as follows:

| Current concept | New product position |
| --- | --- |
| Financial Summary | Overview module / Financial Hero |
| Charts | Overview analytics modules |
| Manual Input creation | Global Add Transaction command |
| Manual Input history/correction | Transactions → Source Detail |
| Transaction Workspace | Transactions workspace |
| Mapping Review | Review workflow |
| Scheduled Input | Automation workspace |
| Merchant/Category pickers | Domain data / controls, not workspaces |
| Feedback capture | Global Send Feedback command |
| Feedback backlog | Feedback workspace |

A new backend capability must not automatically create a new top-level workspace. Top-level workspaces represent durable user activities, not technical subsystems.

## 4. Desktop presentation baseline

Desktop Web is a formal PC product, not an enlarged Mini layout. It should use desktop space, mouse, keyboard, hover, tables/lists, persistent detail panels, and richer analytics where that improves usability.

### 4.1 Desktop shell

The canonical shell is:

~~~text
┌─────────────────────┬──────────────────────────────────────────────┐
│ Sidebar             │ Page Header                                  │
│                     ├──────────────────────────────────────────────│
│ + Add Transaction   │                                              │
│                     │                                              │
│ Overview            │ Main Workspace                               │
│ Transactions        │                                              │
│ Review           3  │                                              │
│ Automation          │                                              │
│                     │                                              │
│ ─────────────────   │                                              │
│ Feedback            │                                              │
│ Settings       future│                                              │
└─────────────────────┴──────────────────────────────────────────────┘
~~~

Sidebar navigation is stable. Page Header contains page-specific actions and context. Main Workspace contains only the current workspace rather than every feature stacked vertically.

### 4.2 Desktop interaction grammar

Keep the number of primary interaction patterns small:

- **Dashboard** — Overview.
- **List + Detail** — Transactions, Review, Feedback.
- **List + Editor** — Automation.
- **Global Modal/Dialog** — Add Transaction, Send Feedback.

For wide Desktop layouts, Transactions/Review/Feedback should favor master-detail presentation. When desktop width becomes narrow, list and detail may switch to navigated views instead of forcing a cramped persistent split pane.

### 4.3 Desktop Overview hierarchy

Overview is read-first. It should prioritize:

1. Financial Hero.
2. Main financial trend/period context.
3. Secondary breakdowns and changes.
4. Needs Attention summary linking to Review.

It must not become another collection of equally weighted KPI cards.

The Financial Hero hierarchy is:

~~~text
Net Cash Flow
-¥102,293.07

Income                     Net Spending
¥0.00                      ¥102,293.07
~~~

Net Cash Flow is the aggregate conclusion. Income and Net Spending are its components.

## 5. Mini presentation baseline

Mini is the formal mobile product presentation. Its UI is designed for touch and small screens. The same Mini presentation is compiled to H5 for desktop-browser preview and to WeChat for the formal Mini Program runtime.

### 5.1 Mini shell

The baseline shell uses four bottom destinations:

- **Overview**
- **Transactions**
- **Review**
- **More**

`More` contains lower-frequency capabilities including Automation and Feedback, with room for future Settings/Data Sources/About without consuming premium bottom-tab positions.

Add Transaction remains a global action rather than a tab. Once that capability is migrated, it may use a visible floating/global action entry plus context-specific `+` actions.

### 5.2 Mini interaction grammar

Mini normally uses navigated pages rather than persistent desktop detail panels:

- Transactions → Transaction Detail.
- Review → Review Detail.
- More → Automation → Rule Editor.
- More → Feedback → Feedback Detail.

Keep normal work depth shallow. Main workflows should usually remain within workspace/list → detail/editor. Low-frequency administration may go one level deeper where necessary.

### 5.3 Mini H5 Preview

The H5 build is a development preview target. The preferred desktop-browser development experience is a centered phone-sized viewport around 390 px wide. Device-width switching may be added later if it creates real testing value.

No requirements should be added solely for mobile-browser deployment, SEO, PWA behavior, mobile Safari support, or a separately distributed mobile Web product.

## 6. Feedback V1

Feedback exists to capture problems with the product itself, not financial-data decisions.

- **Review** = financial data requires a user judgment.
- **Feedback** = the product has a bug, UX problem, data-display problem, or improvement idea.

### 6.1 Feedback creation

V1 optimizes for extremely low capture friction.

Required user field:

- `content`

System-managed fields:

- `id`
- `created_at`
- `status` (`open` / `resolved`, default `open`)
- `context`

Useful automatic context includes:

- current page/workspace
- current entity type and entity id when available
- runtime: `desktop_web`, `mini_h5`, or `weapp`

Do not require type, priority, severity, labels, comments, assignee, or workflow metadata in V1. These may be added only when real use demonstrates value.

### 6.2 Feedback presentation

Desktop:

- Feedback workspace = Inbox + Detail.
- Send Feedback = small global dialog that closes immediately after successful capture.

Mini:

- Feedback workspace lives under More.
- Send Feedback must remain quickly accessible from main work contexts, for example through a page action menu/sheet.

Feedback remains local product data. It must not automatically create GitHub issues. A future explicit promote/export action may bridge selected feedback into development tracking.

## 7. Cross-platform sharing boundary

### 7.1 Always shared where possible

The shared frontend core should own:

- TypeScript API contracts.
- runtime response/request decoding and validation.
- API services independent of transport runtime.
- view-model construction.
- financial/date/status formatting.
- analytics presentation transforms that do not recompute financial facts.
- design tokens.
- reusable test fixtures.

The shared core must not depend on React, Taro, DOM APIs, or Mini Program APIs.

### 7.2 Transport boundary

API service semantics are shared, while transport implementation is runtime-specific.

~~~text
Shared service
     │
HttpTransport contract
  ┌──┴─────────────┐
  │                │
BrowserTransport   TaroTransport
Desktop Web        Mini H5 / WeChat
~~~

Do not scatter `TARO_ENV`, browser checks, or platform branches through shared services.

### 7.3 View-model boundary

Both presentations should consume shared semantic view models but render them differently.

Example:

~~~text
Transaction API model
        ↓
toTransactionListItem()
        ↓
TransactionListItem ViewModel
   ┌────┴────┐
Desktop     Mini
row/table   touch list row
~~~

The same principle applies to Financial Summary, Review, Automation, and Feedback.

### 7.4 Presentation stays separate

Do not require Desktop and Mini to share UI components for:

- navigation/router
- sidebar/tab bar
- table/list layout
- dialog/sheet/page transitions
- form controls
- detail panels/pages
- hover/touch behavior
- platform-specific accessibility or system integration

Start with `apps/web/components` and `apps/mini/components`. Extract presentation components upward only after real duplication proves that sharing is beneficial.

### 7.5 Charts

Share chart-ready semantic series/data preparation when possible. Do not require Desktop and Mini to share a chart renderer. Desktop may use richer hover/tooltips and density; Mini may use simpler touch-oriented presentation.

## 8. Technology baseline V1

The first cross-platform frontend baseline is:

| Area | V1 choice |
| --- | --- |
| Package workspace | npm workspaces |
| Shared language | TypeScript |
| Runtime schema validation | Zod 4 |
| Desktop framework | React 18.3.1 |
| Desktop build | Vite 8 |
| Desktop routing | React Router 7 |
| Desktop UI primitives | shadcn/ui + Radix primitives |
| Desktop styling | Tailwind-compatible shadcn setup suitable for React 18 |
| Mini framework | Taro 4.2.x + React 18.3.1 + TypeScript |
| Mini UI baseline | Taro primitives + project design tokens |
| Unit tests | Vitest |
| Browser E2E/smoke | Playwright |
| Global state library | none initially |
| Chart renderer | not frozen by this baseline |

React is intentionally held at 18.3.1 for the initial cross-platform baseline so Desktop and Taro Mini do not start with different React major versions. Upgrade only when Taro support and the real project benefit justify it.

Do not introduce pnpm/Turborepo/Redux/Zustand/TanStack Query merely as modern-project defaults. Add infrastructure only when the repository demonstrates a real coordination or state-management need.

## 9. Repository frontend structure

Do not split the shared layer into many packages prematurely. The initial physical structure should remain small:

~~~text
frontend/
├── apps/
│   ├── web/
│   └── mini/
└── packages/
    ├── core/
    └── design-tokens/
~~~

`core/` may internally contain contracts, decoders, services, view models, formatting, and presentation transforms without requiring each concern to become its own package.

The root npm workspace should continue to use the repository's existing npm/package-lock workflow.

## 10. Formal API boundary required by the new frontend

The new cross-platform frontend must not depend on reading repository-relative `data/reports/*.json` files directly.

The existing Financial Summary projection should be exposed through the formal Application/HTTP boundary:

~~~text
GET /api/financial-summary
~~~

This is a read operation only. It returns the current generated projection and must not hide a rebuild or mutation behind GET.

Feedback V1 requires:

~~~text
GET   /api/feedback
POST  /api/feedback
PATCH /api/feedback/{id}
~~~

Feedback persistence may use a small local runtime file such as `data/feedback.jsonl`, ignored by Git like other local runtime data.

## 11. Cross-platform frontend POC

The first implementation slice is **Frontend Cross-platform Foundation + Financial Summary + Feedback POC**.

It must be a real vertical slice rather than framework scaffolding only.

### 11.1 In scope

Backend/Application/API:

- expose current Financial Summary through `GET /api/financial-summary`.
- add Feedback persistence and Application/API read/write/update flow.
- preserve the existing backend as the only source of financial truth.

Shared frontend foundation:

- npm workspaces.
- TypeScript.
- `frontend/packages/core` with Zod contracts/decoders, shared services, view models, formatting, and transport abstraction.
- `frontend/packages/design-tokens`.

Desktop:

- React/Vite app shell.
- canonical Desktop navigation.
- real Overview using Financial Summary.
- real Feedback workspace.
- real Send Feedback dialog.
- migration-state placeholders for Transactions/Review/Automation where needed.

Mini:

- Taro React app shell.
- Overview / Transactions / Review / More navigation model.
- real Overview using the same shared Financial Summary semantics.
- real Feedback list/detail and Send Feedback flow.
- H5 development preview.
- WeChat production build.

Legacy:

- preserve `local_dashboard/` as the functional fallback during migration.
- do not iframe it into the new frontend.
- do not partially rewrite it as part of the POC.

### 11.2 Explicitly out of scope

The POC does not migrate:

- Transactions workspace business UI.
- Mapping Review workflow.
- Scheduled Input management.
- Add Transaction implementation.
- existing chart POC.
- income taxonomy design.
- dark mode.
- formal mobile Web product/deployment.
- authentication/public internet deployment.

Shell navigation may show migration-state placeholders for not-yet-migrated workspaces, but nonfunctional controls must not pretend to be complete.

### 11.3 POC success criteria

The POC is successful only when all of the following hold:

1. One root npm install can prepare the frontend workspace deterministically.
2. Shared core has no React/Taro dependency.
3. `GET /api/financial-summary` is the formal client read boundary for Financial Summary.
4. Desktop Overview displays real Financial Summary data through shared contracts/view models.
5. Mini H5 Overview displays the same financial semantics through the same shared core.
6. Desktop can create Feedback and see it in the Feedback inbox.
7. Mini H5 can create Feedback into the same backend inbox.
8. Feedback context distinguishes runtime/page appropriately.
9. Feedback can be resolved and reopened.
10. Taro WeChat production build succeeds.
11. Shared, Desktop, and Mini focused tests pass.
12. Desktop and Mini H5 browser smoke tests pass.
13. Existing Python full suite passes.
14. Existing legacy Dashboard JavaScript/tests remain green.
15. `local_dashboard/` remains functional and is not prematurely removed.

## 12. Design System V1

### 12.1 Visual character

The product visual baseline is:

- **Calm** — no decorative gradients, glassmorphism, or large branded backgrounds.
- **Compact** — meaningful information should fit comfortably on a desktop screen.
- **Precise** — amount, state, hierarchy, and action relationships are immediately readable.
- **Native** — the product should feel like a finance workspace/Mini Program, not a marketing page or generic dashboard template.

V1 is **light mode only**. Theme architecture may remain extensible, but dark-mode implementation is deferred until the new UI is stable.

### 12.2 Color

Use a neutral-first palette with restrained pine/green product identity.

Green is an accent for brand, interaction, selection/focus, and positive semantics rather than a default large-surface background.

Semantic colors include:

- positive
- negative/danger
- warning/attention
- neutral/muted

Most amounts remain primary text by default. Use positive/negative coloring only where direction is materially meaningful. Never rely on color alone to communicate state.

### 12.3 Typography

Use system fonts; do not introduce a web font in V1.

Suggested hierarchy:

- Page title: 24–28 px, semibold.
- Section title: 16–18 px, semibold.
- Body: 14 px.
- Secondary: 13 px.
- Meta: 12 px.
- Hero financial amount: approximately 32–40 px.
- Standard amount: approximately 14–18 px.

Financial numeric text uses tabular numerals. Shared formatters own amount/date/status display semantics.

### 12.4 Spacing and radius

Use a 4 px-based spacing rhythm, primarily:

`4 / 8 / 12 / 16 / 24 / 32 / 40`

Use restrained radius tokens:

- small: 4 px
- medium: 6 px
- large: 8 px
- pill: only where semantically appropriate, such as badges

### 12.5 Elevation and surfaces

Default hierarchy uses spacing, typography, dividers, and subtle borders.

Shadows are reserved for true overlays such as Dialog, Popover, Dropdown, or floating action elements. Ordinary sections, lists, panels, and summaries should not float by default.

Limit surface hierarchy to roughly:

~~~text
Canvas
  ↓
Surface
  ↓
Elevated overlay
~~~

Avoid Card → Inner Card → Input Card nesting.

### 12.6 Card is not the default container

A container should not become a Card merely because it is a section. Prefer hierarchy through spacing, alignment, typography, and dividers.

The most important recurring product primitive is the **List Row**, because Transactions, Review, Automation, and Feedback are all fundamentally list-oriented capabilities.

Desktop and Mini implement List Row separately while sharing semantic content and tokens.

### 12.7 Density

Desktop is mouse/keyboard optimized and information-dense:

- Sidebar approximately 220–240 px.
- common navigation rows approximately 32–36 px high.
- typical desktop controls around 36 px high, with larger primary actions only when useful.

Mini is touch optimized:

- interactive targets approximately 44 px or larger where applicable.
- page horizontal padding around 16 px.
- touch-friendly vertical row rhythm.

Shared tokens do not require identical control dimensions between Desktop and Mini.

### 12.8 Core presentation primitives

V1 should converge on a small set of reliable primitives:

- Navigation
- Financial Hero
- List Row
- Detail Panel / Detail Page
- Form Field
- Dialog / Sheet
- Badge / Status
- Loading / Empty / Error state

Buttons use only a small semantic family:

- Primary
- Secondary
- Ghost
- Danger

Accessibility is baseline behavior: visible focus, semantic controls and labels, keyboard-friendly Desktop interaction, adequate contrast, touch-friendly Mini targets, and no color-only state communication.

## 13. Migration strategy

Do not replace the existing frontend in one rewrite.

Migration proceeds by complete vertical capabilities:

1. establish cross-platform foundation with Financial Summary + Feedback.
2. validate the Desktop and Mini presentation architecture using real data and real write flow.
3. migrate subsequent capabilities one coherent workspace/workflow at a time.
4. retire equivalent `local_dashboard/` functionality only after the replacement has real validation coverage.
5. remove the legacy Dashboard only when all required functionality has migrated and regression coverage proves parity.

The new frontend should improve presentation architecture without destabilizing the already-validated Python domain pipeline.

## 14. Decision status

This document is the V1 canonical frontend product architecture baseline for the upcoming POC. Implementation may refine low-level details when repository/toolchain facts require it, but changes to the following require an explicit design decision rather than accidental drift:

- canonical workspaces and global commands
- Desktop vs Mini product/presentation distinction
- H5 as preview-only runtime
- backend/shared/presentation ownership boundary
- shared-core no-React/Taro dependency rule
- POC vertical scope and legacy coexistence strategy
- Design System principles, especially neutral-first hierarchy and no-card-by-default
