import type { ApiFetch } from "./types";

export const settingsApi = {
  get: (api: ApiFetch) => api("/api/v1/settings"),
  save: (api: ApiFetch, settings: object) => api("/api/v1/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  }),
  validate: (api: ApiFetch, settings: object) => api("/api/v1/settings/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  }),
  models: (api: ApiFetch, provider: string, settings: object) => api(`/api/v1/settings/models/${provider}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  }),
  subscriptionStatus: (api: ApiFetch) => api("/api/v1/settings/subscription-status"),
  subscriptionLogin: (api: ApiFetch, provider: string) => api(`/api/v1/settings/subscription-login/${provider}`, { method: "POST" }),
};
