import { useEffect, useState } from "react";
import {
  Archive,
  Download,
  ExternalLink,
  FileText,
  Folder,
  Forward,
  Inbox,
  Mail,
  MailOpen,
  Paperclip,
  PenSquare,
  RefreshCw,
  Reply,
  ReplyAll,
  Search,
  Send,
  Trash2,
} from "lucide-react";
import { outlookApi } from "../api/outlook.js";
import ComposeDialog from "../components/ComposeDialog.jsx";
import HtmlBody from "../components/HtmlBody.jsx";
import { Badge, Button, EmptyState, ErrorBanner, Input, Loading, Notice, Select, Spinner, Toggle, cx } from "../components/ui.jsx";
import { useAction, useRequest } from "../hooks/useApi.js";
import { downloadBlob } from "../lib/download.js";
import { formatBytes, formatDateTime, formatRelativeDay } from "../lib/format.js";

const FOLDER_ICONS = {
  inbox: Inbox,
  archive: Archive,
  "sent items": Send,
  drafts: FileText,
  "deleted items": Trash2,
};

const INBOX = { id: "inbox", display_name: "Inbox" };

export default function MailPage() {
  const [folder, setFolder] = useState(INBOX);
  const [searchText, setSearchText] = useState("");
  const [search, setSearch] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [compose, setCompose] = useState(null);
  const [notice, setNotice] = useState(null);

  const folders = useRequest((token) => outlookApi.folders(token), []);
  const messages = useRequest(
    (token) =>
      outlookApi.messages(token, {
        folder: folder.id,
        unread_only: search ? false : unreadOnly,
        search: search || undefined,
        top: 50,
      }),
    [folder.id, unreadOnly, search],
  );

  const selectFolder = (next) => {
    setFolder(next);
    setSelectedId(null);
    setSearch("");
    setSearchText("");
  };

  const afterMutation = (text) => {
    setNotice(text);
    setSelectedId(null);
    messages.reload();
    folders.reload();
  };

  return (
    <div className="flex h-full min-h-0">
      {/* Folders */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-gray-200 bg-white">
        <div className="p-3">
          <Button className="w-full" onClick={() => setCompose({ mode: "new" })}>
            <PenSquare size={16} /> New message
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {folders.loading && !folders.data && <Loading label="Folders…" />}
          <ErrorBanner error={folders.error} onRetry={folders.reload} className="m-2" />
          {(folders.data || [INBOX]).map((f) => {
            const Icon = FOLDER_ICONS[f.display_name?.toLowerCase()] || Folder;
            const active = f.id === folder.id || (folder.id === "inbox" && f.display_name?.toLowerCase() === "inbox");
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => selectFolder(f)}
                className={cx(
                  "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm",
                  active ? "bg-brand-dim font-medium text-brand" : "text-gray-700 hover:bg-gray-100",
                )}
              >
                <Icon size={16} className="shrink-0" />
                <span className="flex-1 truncate">{f.display_name}</span>
                {f.unread_item_count > 0 && <Badge tone={active ? "brand" : "gray"}>{f.unread_item_count}</Badge>}
              </button>
            );
          })}
        </div>
      </aside>

      {/* Message list */}
      <section className="flex w-96 shrink-0 flex-col border-r border-gray-200 bg-white">
        <div className="space-y-2 border-b border-gray-100 p-3">
          <form
            className="relative"
            onSubmit={(e) => {
              e.preventDefault();
              setSearch(searchText.trim());
              setSelectedId(null);
            }}
          >
            <Search size={16} className="pointer-events-none absolute left-3 top-2.5 text-gray-400" />
            <Input
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search — try from:ada or subject:invoice"
              className="pl-9"
            />
          </form>
          <div className="flex items-center justify-between">
            <Toggle checked={unreadOnly && !search} onChange={setUnreadOnly} label="Unread only" />
            <div className="flex items-center gap-1">
              {search && (
                <Button variant="ghost" size="sm" onClick={() => { setSearch(""); setSearchText(""); }}>
                  Clear search
                </Button>
              )}
              <Button variant="ghost" size="icon" onClick={messages.reload} title="Refresh" aria-label="Refresh">
                <RefreshCw size={16} className={messages.loading ? "animate-spin" : ""} />
              </Button>
            </div>
          </div>
          {notice && <Notice notice={notice} onDismiss={() => setNotice(null)} />}
        </div>
        <div className="flex-1 overflow-y-auto">
          {messages.loading && !messages.data && <Loading label="Messages…" />}
          <ErrorBanner error={messages.error} onRetry={messages.reload} className="m-3" />
          {messages.data?.length === 0 && (
            <EmptyState icon={Mail} title={search ? "No matches" : "Nothing here"} hint={search ? "Graph search is relevance-ranked; try fewer words." : undefined} />
          )}
          {messages.data?.map((m) => (
            <MessageRow key={m.id} message={m} active={m.id === selectedId} onClick={() => setSelectedId(m.id)} />
          ))}
        </div>
      </section>

      {/* Reading pane */}
      <section className="flex min-w-0 flex-1 flex-col bg-white">
        {selectedId ? (
          <MessageView
            key={selectedId}
            messageId={selectedId}
            folders={folders.data || []}
            onRead={(read) =>
              messages.setData((list) => list?.map((m) => (m.id === selectedId ? { ...m, is_read: read } : m)))
            }
            onCompose={(mode, message) => setCompose({ mode, message })}
            onMutated={afterMutation}
          />
        ) : (
          <EmptyState icon={MailOpen} title="Select a message" hint="Search accepts Graph KQL such as from:, subject:, hasAttachments:true." />
        )}
      </section>

      {compose && (
        <ComposeDialog
          mode={compose.mode}
          message={compose.message}
          onClose={() => setCompose(null)}
          onDone={(text) => {
            setCompose(null);
            setNotice(text);
            messages.reload();
          }}
        />
      )}
    </div>
  );
}

function MessageRow({ message, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        "flex w-full flex-col gap-0.5 border-b border-gray-100 px-4 py-3 text-left hover:bg-gray-50",
        active && "bg-brand-dim",
      )}
    >
      <div className="flex items-center gap-2">
        <span className={cx("h-2 w-2 shrink-0 rounded-full", message.is_read ? "bg-transparent" : "bg-brand")} />
        <span className={cx("flex-1 truncate text-sm", message.is_read ? "text-gray-700" : "font-semibold text-gray-900")}>
          {message.from_name || message.from_address || "Unknown sender"}
        </span>
        <span className="shrink-0 text-xs text-gray-400">{formatRelativeDay(message.received_at)}</span>
      </div>
      <div className="flex items-center gap-2 pl-4">
        <span className={cx("flex-1 truncate text-sm", message.is_read ? "text-gray-600" : "text-gray-900")}>
          {message.subject || "(no subject)"}
        </span>
        {message.has_attachments && <Paperclip size={14} className="shrink-0 text-gray-400" />}
      </div>
      <div className="truncate pl-4 text-xs text-gray-400">{message.body_preview}</div>
    </button>
  );
}

function MessageView({ messageId, folders, onRead, onCompose, onMutated }) {
  const detail = useRequest((token) => outlookApi.message(token, messageId), [messageId]);
  const attachments = useRequest((token) => outlookApi.attachments(token, messageId), [messageId], {
    enabled: Boolean(detail.data?.has_attachments),
  });
  const { run, busy, error, clearError } = useAction();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const message = detail.data;

  // Opening an unread message marks it read, as a mail client would.
  useEffect(() => {
    if (message && !message.is_read) {
      run("read", (token) => outlookApi.setRead(token, messageId, true)).then(() => onRead(true));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [message?.id]);

  const markUnread = async () => {
    const ok = await run("unread", (token) => outlookApi.setRead(token, messageId, false).then(() => true));
    if (ok) onRead(false);
  };

  const move = async (destination) => {
    if (!destination) return;
    const moved = await run("move", (token) => outlookApi.move(token, messageId, destination));
    if (moved) onMutated("Message moved");
  };

  const remove = async () => {
    const ok = await run("delete", (token) => outlookApi.remove(token, messageId).then(() => true));
    if (ok) onMutated("Moved to Deleted Items");
  };

  const download = (attachment) =>
    run("download", (token) => outlookApi.attachmentContent(token, messageId, attachment.id).then(downloadBlob));

  if (detail.loading && !message) return <Loading label="Opening message…" />;
  if (detail.error) return <ErrorBanner error={detail.error} onRetry={detail.reload} className="m-4" />;
  if (!message) return null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b border-gray-100 px-4 py-2">
        <Button variant="ghost" size="sm" onClick={() => onCompose("reply", message)}><Reply size={15} /> Reply</Button>
        <Button variant="ghost" size="sm" onClick={() => onCompose("replyAll", message)}><ReplyAll size={15} /> Reply all</Button>
        <Button variant="ghost" size="sm" onClick={() => onCompose("forward", message)}><Forward size={15} /> Forward</Button>
        <span className="mx-1 h-5 w-px bg-gray-200" />
        <Button variant="ghost" size="sm" onClick={() => move("archive")} disabled={busy !== null}><Archive size={15} /> Archive</Button>
        <Select className="w-auto py-1 text-xs" value="" onChange={(e) => move(e.target.value)} disabled={busy !== null} aria-label="Move to folder">
          <option value="">Move to…</option>
          {folders.map((f) => <option key={f.id} value={f.id}>{f.display_name}</option>)}
        </Select>
        <Button variant="ghost" size="sm" onClick={markUnread} disabled={busy !== null}><Mail size={15} /> Mark unread</Button>
        <span className="flex-1" />
        {confirmDelete ? (
          <>
            <Button variant="danger" size="sm" onClick={remove} disabled={busy !== null}>Confirm delete</Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(false)}>Cancel</Button>
          </>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(true)} disabled={busy !== null}><Trash2 size={15} /> Delete</Button>
        )}
      </div>

      <div className="space-y-1 border-b border-gray-100 px-6 py-4">
        <ErrorBanner error={error} onDismiss={clearError} />
        <div className="flex items-start justify-between gap-4">
          <h2 className="text-lg font-semibold">{message.subject || "(no subject)"}</h2>
          {message.web_link && (
            <a href={message.web_link} target="_blank" rel="noreferrer" className="flex shrink-0 items-center gap-1 text-xs text-gray-500 hover:text-brand">
              <ExternalLink size={14} /> Open in Outlook
            </a>
          )}
        </div>
        <div className="text-sm text-gray-700">
          <span className="font-medium">{message.from_name || message.from_address}</span>
          {message.from_name && message.from_address && <span className="text-gray-500"> &lt;{message.from_address}&gt;</span>}
        </div>
        <Recipients label="To" list={message.to_recipients} />
        <Recipients label="Cc" list={message.cc_recipients} />
        <div className="text-xs text-gray-400">{formatDateTime(message.received_at)}</div>
      </div>

      {message.has_attachments && (
        <div className="flex flex-wrap items-center gap-2 border-b border-gray-100 px-6 py-2">
          {attachments.loading && <Spinner />}
          {attachments.data?.map((a) => (
            <button
              key={a.id}
              type="button"
              onClick={() => download(a)}
              disabled={busy !== null}
              className="flex items-center gap-1.5 rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-700 hover:bg-gray-50"
              title="Download"
            >
              <Paperclip size={12} /> {a.name} <span className="text-gray-400">{formatBytes(a.size)}</span> <Download size={12} />
            </button>
          ))}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {message.body_content_type === "html" ? (
          <HtmlBody html={message.body_content || ""} />
        ) : (
          <pre className="whitespace-pre-wrap px-6 py-4 font-body text-sm text-gray-800">{message.body_content}</pre>
        )}
      </div>
    </div>
  );
}

function Recipients({ label, list }) {
  if (!list || list.length === 0) return null;
  return (
    <div className="text-xs text-gray-500">
      <span className="font-medium">{label}:</span>{" "}
      {list.map((r) => r.name ? `${r.name} <${r.address}>` : r.address).join(", ")}
    </div>
  );
}
