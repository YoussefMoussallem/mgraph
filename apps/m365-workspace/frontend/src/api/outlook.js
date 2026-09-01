// Outlook endpoints — /api/v1/outlook/*, backed by ``outlook-client``.
import { apiFetch, apiFetchBlob, apiUpload, query } from "./client.js";

const enc = encodeURIComponent;
const BASE = "/v1/outlook";

export const outlookApi = {
  profile: (token) => apiFetch(`${BASE}/profile`, { token }),
  folders: (token) => apiFetch(`${BASE}/folders${query({ top: 50 })}`, { token }),

  // params: { top, folder, unread_only, search }
  messages: (token, params) => apiFetch(`${BASE}/messages${query(params)}`, { token }),
  message: (token, id) => apiFetch(`${BASE}/messages/${enc(id)}`, { token }),
  send: (token, body) => apiFetch(`${BASE}/messages/send`, { token, method: "POST", body }),
  createDraft: (token, body) =>
    apiFetch(`${BASE}/messages/drafts`, { token, method: "POST", body }),
  sendDraft: (token, id) => apiFetch(`${BASE}/messages/${enc(id)}/send`, { token, method: "POST" }),
  reply: (token, id, body) =>
    apiFetch(`${BASE}/messages/${enc(id)}/reply`, { token, method: "POST", body }),
  forward: (token, id, body) =>
    apiFetch(`${BASE}/messages/${enc(id)}/forward`, { token, method: "POST", body }),
  move: (token, id, destination) =>
    apiFetch(`${BASE}/messages/${enc(id)}/move`, {
      token,
      method: "POST",
      body: { destination_folder: destination },
    }),
  remove: (token, id, { permanent = false } = {}) =>
    apiFetch(`${BASE}/messages/${enc(id)}${query({ permanent })}`, { token, method: "DELETE" }),
  setRead: (token, id, read) =>
    apiFetch(`${BASE}/messages/${enc(id)}/read`, { token, method: "PATCH", body: { read } }),

  attachments: (token, id) => apiFetch(`${BASE}/messages/${enc(id)}/attachments`, { token }),
  attachmentContent: (token, id, attachmentId) =>
    apiFetchBlob(`${BASE}/messages/${enc(id)}/attachments/${enc(attachmentId)}/content`, { token }),
  addAttachment: (token, id, file) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    return apiUpload(`${BASE}/messages/${enc(id)}/attachments`, { token, formData });
  },

  // params: { start, end, top }
  events: (token, params) => apiFetch(`${BASE}/events${query(params)}`, { token }),
  event: (token, id) => apiFetch(`${BASE}/events/${enc(id)}`, { token }),
  createEvent: (token, body) => apiFetch(`${BASE}/events`, { token, method: "POST", body }),
  updateEvent: (token, id, body) =>
    apiFetch(`${BASE}/events/${enc(id)}`, { token, method: "PATCH", body }),
  respondEvent: (token, id, body) =>
    apiFetch(`${BASE}/events/${enc(id)}/respond`, { token, method: "POST", body }),
  deleteEvent: (token, id) => apiFetch(`${BASE}/events/${enc(id)}`, { token, method: "DELETE" }),

  // params: { top, name_starts_with }
  contacts: (token, params) => apiFetch(`${BASE}/contacts${query(params)}`, { token }),
};
