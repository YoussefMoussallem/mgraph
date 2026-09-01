// SharePoint endpoints — /api/v1/sharepoint/*, backed by ``sharepoint-client``.
import { apiFetch, apiFetchBlob, apiUpload, query } from "./client.js";

const enc = encodeURIComponent;
const BASE = "/v1/sharepoint";

export const sharepointApi = {
  sites: (token, params) => apiFetch(`${BASE}/sites${query(params)}`, { token }),
  site: (token, siteId) => apiFetch(`${BASE}/sites/${enc(siteId)}`, { token }),
  drives: (token, siteId) => apiFetch(`${BASE}/sites/${enc(siteId)}/drives${query({ top: 50 })}`, { token }),
  lists: (token, siteId) => apiFetch(`${BASE}/sites/${enc(siteId)}/lists`, { token }),
  listItems: (token, siteId, listId) =>
    apiFetch(`${BASE}/sites/${enc(siteId)}/lists/${enc(listId)}/items`, { token }),

  // params: { path, item_id, top }
  items: (token, driveId, params) =>
    apiFetch(`${BASE}/drives/${enc(driveId)}/items${query(params)}`, { token }),
  item: (token, driveId, itemId) =>
    apiFetch(`${BASE}/drives/${enc(driveId)}/items/${enc(itemId)}`, { token }),
  itemByPath: (token, driveId, path) =>
    apiFetch(`${BASE}/drives/${enc(driveId)}/item-by-path${query({ path })}`, { token }),
  content: (token, driveId, itemId) =>
    apiFetchBlob(`${BASE}/drives/${enc(driveId)}/items/${enc(itemId)}/content`, { token }),
  search: (token, driveId, q) =>
    apiFetch(`${BASE}/drives/${enc(driveId)}/search${query({ q, top: 50 })}`, { token }),

  upload: (token, driveId, file, { parentPath, parentItemId, conflict = "rename" } = {}) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    if (parentItemId) formData.append("parent_item_id", parentItemId);
    else if (parentPath) formData.append("parent_path", parentPath);
    formData.append("conflict", conflict);
    return apiUpload(`${BASE}/drives/${enc(driveId)}/upload`, { token, formData });
  },
  createFolder: (token, driveId, body) =>
    apiFetch(`${BASE}/drives/${enc(driveId)}/folders`, { token, method: "POST", body }),
  moveItem: (token, driveId, itemId, body) =>
    apiFetch(`${BASE}/drives/${enc(driveId)}/items/${enc(itemId)}`, { token, method: "PATCH", body }),
  deleteItem: (token, driveId, itemId) =>
    apiFetch(`${BASE}/drives/${enc(driveId)}/items/${enc(itemId)}`, { token, method: "DELETE" }),
};
