/** Save a blob from ``apiFetchBlob`` through a transient anchor click. */
export function downloadBlob({ blob, filename }) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "download";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const TEXT_TYPES = new Set([
  "application/json",
  "application/xml",
  "application/javascript",
  "application/x-javascript",
  "application/yaml",
  "application/x-yaml",
  "application/csv",
  "application/sql",
]);

/** Whether a MIME type is worth rendering inline as text. */
export function isTextType(mime) {
  if (!mime) return false;
  const type = mime.split(";")[0].trim().toLowerCase();
  return type.startsWith("text/") || TEXT_TYPES.has(type) || type.endsWith("+json") || type.endsWith("+xml");
}
