const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const timeOnly = new Intl.DateTimeFormat(undefined, { timeStyle: "short" });
const weekday = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" });

function asDate(value) {
  if (value instanceof Date) return value;
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDateTime(value) {
  const d = asDate(value);
  return d ? dateTime.format(d) : "";
}

export function formatDate(value) {
  const d = asDate(value);
  return d ? dateOnly.format(d) : "";
}

export function formatTime(value) {
  const d = asDate(value);
  return d ? timeOnly.format(d) : "";
}

export function formatWeekday(value) {
  const d = asDate(value);
  return d ? weekday.format(d) : "";
}

/** "Today 09:14" / "Yesterday" / a date — for message lists. */
export function formatRelativeDay(value) {
  const d = asDate(value);
  if (!d) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return timeOnly.format(d);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return dateOnly.format(d);
}

export function formatBytes(n) {
  if (n === null || n === undefined) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/**
 * Graph pairs a zone-less wall-clock string with a zone name. Responses
 * come back in UTC unless a time-zone preference is sent (the SDK sends
 * none), so a UTC event converts exactly; anything else is shown as the
 * wall-clock time it carries.
 */
export function eventDate(event, which) {
  const raw = event?.[which];
  if (!raw) return null;
  const base = raw.length > 19 ? raw.slice(0, 19) : raw;
  const zone = (event.time_zone || "UTC").toUpperCase();
  return asDate(zone === "UTC" ? `${base}Z` : base);
}

export function initials(name) {
  if (!name) return "?";
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

/** Local ``datetime-local`` input value for a Date. */
export function toLocalInput(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
