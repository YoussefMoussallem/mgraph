// Assistant endpoints — /api/v1/agent/*, backed by ``m365-langchain-tools``.
import { apiFetch, query } from "./client.js";

const BASE = "/v1/agent";

export const agentApi = {
  status: (token) => apiFetch(`${BASE}/status`, { token }),
  tools: (token, { includeWrites = true } = {}) =>
    apiFetch(`${BASE}/tools${query({ include_writes: includeWrites })}`, { token }),
  // messages: [{ role: "user"|"assistant", content }], the last one from the user.
  chat: (token, { messages, includeWrites }) =>
    apiFetch(`${BASE}/chat`, {
      token,
      method: "POST",
      body: { messages, include_writes: includeWrites },
    }),
};
