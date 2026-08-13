import { FamilySpendingService } from "@family-spending/core";

import { BrowserTransport } from "./browser-transport";

/** Keep the production client on relative /api paths so Vite proxy and future reverse proxy share one contract. */
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim() ?? "";

export const familySpendingService = new FamilySpendingService(
  new BrowserTransport(apiBaseUrl),
);
