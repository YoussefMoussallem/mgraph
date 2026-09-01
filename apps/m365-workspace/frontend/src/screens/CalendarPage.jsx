import { useMemo, useState } from "react";
import { CalendarDays, CalendarPlus, Check, ChevronLeft, ChevronRight, ExternalLink, HelpCircle, MapPin, Trash2, Video, X } from "lucide-react";
import { outlookApi } from "../api/outlook.js";
import { Badge, Button, EmptyState, ErrorBanner, Field, Input, Loading, Modal, Notice, PageHeader, Textarea, Toggle, cx } from "../components/ui.jsx";
import { useAction, useRequest } from "../hooks/useApi.js";
import { browserTimeZone, eventDate, formatTime, formatWeekday, toLocalInput } from "../lib/format.js";

function startOfWeek(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // Monday
  return d;
}

function addDays(date, days) {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}

const RESPONSE_TONES = { accepted: "green", organizer: "brand", tentativelyAccepted: "amber", declined: "red", notResponded: "gray", none: "gray" };

export default function CalendarPage() {
  const [weekStart, setWeekStart] = useState(() => startOfWeek(new Date()));
  const [selected, setSelected] = useState(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState(null);
  const weekEnd = useMemo(() => addDays(weekStart, 7), [weekStart]);

  const events = useRequest(
    (token) => outlookApi.events(token, { start: weekStart.toISOString(), end: weekEnd.toISOString(), top: 50 }),
    [weekStart.getTime()],
  );

  const byDay = useMemo(() => {
    const groups = new Map();
    for (let i = 0; i < 7; i += 1) groups.set(addDays(weekStart, i).toDateString(), []);
    for (const event of events.data || []) {
      const start = eventDate(event, "start");
      const key = start ? start.toDateString() : "unknown";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    }
    return [...groups.entries()];
  }, [events.data, weekStart]);

  const afterMutation = (text) => {
    setNotice(text);
    setSelected(null);
    events.reload();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Calendar" subtitle={`${formatWeekday(weekStart)} – ${formatWeekday(addDays(weekStart, 6))}`}>
        <Button variant="secondary" size="icon" onClick={() => setWeekStart(addDays(weekStart, -7))} aria-label="Previous week"><ChevronLeft size={16} /></Button>
        <Button variant="secondary" size="sm" onClick={() => setWeekStart(startOfWeek(new Date()))}>Today</Button>
        <Button variant="secondary" size="icon" onClick={() => setWeekStart(addDays(weekStart, 7))} aria-label="Next week"><ChevronRight size={16} /></Button>
        <Button onClick={() => setCreating(true)}><CalendarPlus size={16} /> New event</Button>
      </PageHeader>

      <div className="flex min-h-0 flex-1">
        <div className="flex-1 overflow-y-auto p-6">
          {notice && <div className="mb-4"><Notice notice={notice} onDismiss={() => setNotice(null)} /></div>}
          <ErrorBanner error={events.error} onRetry={events.reload} className="mb-4" />
          {events.loading && !events.data && <Loading label="Calendar…" />}
          <div className="space-y-6">
            {byDay.map(([key, list]) => (
              <div key={key}>
                <h3 className={cx("mb-2 text-sm font-semibold", new Date(key).toDateString() === new Date().toDateString() ? "text-brand" : "text-gray-700")}>
                  {formatWeekday(new Date(key))}
                </h3>
                {list.length === 0 ? (
                  <div className="text-xs text-gray-400">No events</div>
                ) : (
                  <div className="space-y-2">
                    {list.map((event) => (
                      <button
                        key={event.id}
                        type="button"
                        onClick={() => setSelected(event)}
                        className={cx(
                          "flex w-full items-center gap-3 rounded-lg border bg-white px-4 py-3 text-left hover:border-brand/40",
                          selected?.id === event.id ? "border-brand" : "border-gray-200",
                          event.is_cancelled && "opacity-60 line-through",
                        )}
                      >
                        <div className="w-28 shrink-0 text-sm text-gray-600">
                          {event.is_all_day ? "All day" : `${formatTime(eventDate(event, "start"))} – ${formatTime(eventDate(event, "end"))}`}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium">{event.subject || "(no subject)"}</div>
                          <div className="truncate text-xs text-gray-500">
                            {event.organizer?.name || event.organizer?.address}
                            {event.location && ` · ${event.location}`}
                          </div>
                        </div>
                        {event.is_online_meeting && <Video size={16} className="text-gray-400" />}
                        <Badge tone={RESPONSE_TONES[event.response_status] || "gray"}>{event.response_status || "—"}</Badge>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <aside className="w-96 shrink-0 border-l border-gray-200 bg-white">
          {selected ? (
            <EventDetail key={selected.id} event={selected} onMutated={afterMutation} onClose={() => setSelected(null)} />
          ) : (
            <EmptyState icon={CalendarDays} title="Select an event" hint="Respond to invitations, open the Teams link, or cancel your own events." />
          )}
        </aside>
      </div>

      {creating && (
        <EventDialog
          onClose={() => setCreating(false)}
          onDone={() => {
            setCreating(false);
            afterMutation("Event created — attendees have been invited");
          }}
        />
      )}
    </div>
  );
}

function EventDetail({ event, onMutated, onClose }) {
  const { run, busy, error } = useAction();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const isOrganizer = event.response_status === "organizer";

  const respond = async (response) => {
    const ok = await run(response, (token) => outlookApi.respondEvent(token, event.id, { response }).then(() => true));
    if (ok) onMutated(`Invitation ${response === "tentative" ? "marked tentative" : `${response}ed`}`);
  };

  const remove = async () => {
    const ok = await run("delete", (token) => outlookApi.deleteEvent(token, event.id).then(() => true));
    if (ok) onMutated(isOrganizer ? "Event cancelled — attendees have been notified" : "Removed from your calendar");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-2 border-b border-gray-100 px-5 py-4">
        <div>
          <h2 className="text-base font-semibold">{event.subject || "(no subject)"}</h2>
          <div className="text-sm text-gray-600">
            {formatWeekday(eventDate(event, "start"))}
            {!event.is_all_day && `, ${formatTime(eventDate(event, "start"))} – ${formatTime(eventDate(event, "end"))}`}
          </div>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close"><X size={16} /></Button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4 text-sm">
        <ErrorBanner error={error} />
        {event.location && <div className="flex items-center gap-2 text-gray-700"><MapPin size={15} className="text-gray-400" /> {event.location}</div>}
        {event.online_meeting_url && (
          <a href={event.online_meeting_url} target="_blank" rel="noreferrer" className="flex items-center gap-2 text-brand hover:underline">
            <Video size={15} /> Join online meeting
          </a>
        )}
        <div>
          <div className="text-xs font-medium text-gray-500">Organizer</div>
          <div>{event.organizer?.name || event.organizer?.address || "—"}</div>
        </div>
        {event.attendees?.length > 0 && (
          <div>
            <div className="text-xs font-medium text-gray-500">Attendees</div>
            <ul className="mt-1 space-y-1">
              {event.attendees.map((a) => (
                <li key={a.address} className="flex items-center justify-between gap-2">
                  <span className="truncate">{a.name || a.address}</span>
                  <Badge tone={RESPONSE_TONES[a.response] || "gray"}>{a.response || "—"}</Badge>
                </li>
              ))}
            </ul>
          </div>
        )}
        {event.body_preview && <p className="whitespace-pre-wrap text-gray-600">{event.body_preview}</p>}
        {event.web_link && (
          <a href={event.web_link} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-gray-500 hover:text-brand">
            <ExternalLink size={13} /> Open in Outlook
          </a>
        )}
      </div>

      <div className="space-y-2 border-t border-gray-100 px-5 py-3">
        {!isOrganizer && (
          <div className="flex gap-2">
            <Button size="sm" className="flex-1" onClick={() => respond("accept")} disabled={busy !== null}><Check size={14} /> Accept</Button>
            <Button size="sm" variant="secondary" className="flex-1" onClick={() => respond("tentative")} disabled={busy !== null}><HelpCircle size={14} /> Tentative</Button>
            <Button size="sm" variant="secondary" className="flex-1" onClick={() => respond("decline")} disabled={busy !== null}><X size={14} /> Decline</Button>
          </div>
        )}
        {confirmDelete ? (
          <div className="flex gap-2">
            <Button size="sm" variant="danger" className="flex-1" onClick={remove} disabled={busy !== null}>
              {isOrganizer ? "Cancel for everyone" : "Remove from my calendar"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>Keep</Button>
          </div>
        ) : (
          <Button size="sm" variant="ghost" className="w-full" onClick={() => setConfirmDelete(true)} disabled={busy !== null}>
            <Trash2 size={14} /> {isOrganizer ? "Cancel event" : "Remove"}
          </Button>
        )}
      </div>
    </div>
  );
}

function EventDialog({ onClose, onDone }) {
  const { run, busy, error } = useAction();
  const defaultStart = useMemo(() => {
    const d = new Date();
    d.setMinutes(0, 0, 0);
    d.setHours(d.getHours() + 1);
    return d;
  }, []);
  const [subject, setSubject] = useState("");
  const [start, setStart] = useState(toLocalInput(defaultStart));
  const [end, setEnd] = useState(toLocalInput(new Date(defaultStart.getTime() + 30 * 60 * 1000)));
  const [location, setLocation] = useState("");
  const [attendees, setAttendees] = useState("");
  const [body, setBody] = useState("");
  const [online, setOnline] = useState(false);
  const [allDay, setAllDay] = useState(false);

  const create = async () => {
    const created = await run("create", (token) =>
      outlookApi.createEvent(token, {
        subject,
        start: allDay ? `${start.slice(0, 10)}T00:00:00` : `${start}:00`,
        end: allDay ? `${end.slice(0, 10)}T00:00:00` : `${end}:00`,
        time_zone: browserTimeZone,
        location: location || null,
        attendees: attendees.split(/[,;\s]+/).map((a) => a.trim()).filter(Boolean),
        body: body || null,
        is_all_day: allDay,
        online_meeting: online,
      }),
    );
    if (created) onDone(created);
  };

  return (
    <Modal
      title="New event"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={create} disabled={busy !== null || !subject.trim()}>{busy ? "Creating…" : "Create & invite"}</Button>
        </>
      }
    >
      <div className="space-y-3">
        <ErrorBanner error={error} />
        <Field label="Subject"><Input value={subject} onChange={(e) => setSubject(e.target.value)} autoFocus /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Start"><Input type={allDay ? "date" : "datetime-local"} value={allDay ? start.slice(0, 10) : start} onChange={(e) => setStart(allDay ? `${e.target.value}T00:00` : e.target.value)} /></Field>
          <Field label="End"><Input type={allDay ? "date" : "datetime-local"} value={allDay ? end.slice(0, 10) : end} onChange={(e) => setEnd(allDay ? `${e.target.value}T00:00` : e.target.value)} /></Field>
        </div>
        <div className="text-xs text-gray-400">Times are in {browserTimeZone}.</div>
        <Field label="Attendees" hint="Comma-separated addresses; each receives an invitation."><Input value={attendees} onChange={(e) => setAttendees(e.target.value)} placeholder="a@company.com, b@company.com" /></Field>
        <Field label="Location"><Input value={location} onChange={(e) => setLocation(e.target.value)} /></Field>
        <Field label="Notes"><Textarea value={body} onChange={(e) => setBody(e.target.value)} className="min-h-20" /></Field>
        <div className="flex gap-6">
          <Toggle checked={online} onChange={setOnline} label="Teams meeting" />
          <Toggle checked={allDay} onChange={setAllDay} label="All day" />
        </div>
      </div>
    </Modal>
  );
}
