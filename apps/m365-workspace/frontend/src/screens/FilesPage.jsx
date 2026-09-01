import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronRight,
  Download,
  ExternalLink,
  File as FileIcon,
  FileText,
  Folder,
  FolderPlus,
  FolderSymlink,
  Globe,
  HardDrive,
  List as ListIcon,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { sharepointApi } from "../api/sharepoint.js";
import { Badge, Button, EmptyState, ErrorBanner, Field, Input, Loading, Modal, Notice, Select, Spinner, cx } from "../components/ui.jsx";
import { useAction, useRequest } from "../hooks/useApi.js";
import { downloadBlob, isTextType } from "../lib/download.js";
import { formatBytes, formatDateTime } from "../lib/format.js";

export default function FilesPage() {
  const [siteQuery, setSiteQuery] = useState("");
  const [siteSearch, setSiteSearch] = useState("");
  const [site, setSite] = useState(null);
  const [drive, setDrive] = useState(null);
  const [view, setView] = useState("files");

  const sites = useRequest((token) => sharepointApi.sites(token, { q: siteSearch || undefined, top: 25 }), [siteSearch]);
  const drives = useRequest((token) => sharepointApi.drives(token, site.id), [site?.id], { enabled: Boolean(site) });

  useEffect(() => {
    if (drives.data?.length && !drive) setDrive(drives.data[0]);
  }, [drives.data, drive]);

  const pickSite = (next) => {
    setSite(next);
    setDrive(null);
    setView("files");
  };

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-72 shrink-0 flex-col border-r border-gray-200 bg-white">
        <form
          className="relative border-b border-gray-100 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            setSiteSearch(siteQuery.trim());
          }}
        >
          <Search size={16} className="pointer-events-none absolute left-6 top-5.5 text-gray-400" />
          <Input value={siteQuery} onChange={(e) => setSiteQuery(e.target.value)} placeholder="Search sites…" className="pl-9" />
        </form>
        <div className="flex-1 overflow-y-auto p-2">
          {sites.loading && !sites.data && <Loading label="Sites…" />}
          <ErrorBanner error={sites.error} onRetry={sites.reload} className="m-2" />
          {sites.data?.length === 0 && <div className="p-3 text-xs text-gray-400">No sites match.</div>}
          {sites.data?.map((s) => (
            <div key={s.id}>
              <button
                type="button"
                onClick={() => pickSite(s)}
                className={cx(
                  "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm",
                  site?.id === s.id ? "bg-brand-dim font-medium text-brand" : "text-gray-700 hover:bg-gray-100",
                )}
              >
                <Globe size={15} className="shrink-0" />
                <span className="truncate">{s.display_name || s.name}</span>
              </button>
              {site?.id === s.id && (
                <div className="ml-4 space-y-0.5 border-l border-gray-100 pl-2">
                  {drives.loading && <div className="px-3 py-1"><Spinner /></div>}
                  {drives.data?.map((d) => (
                    <button
                      key={d.id}
                      type="button"
                      onClick={() => { setDrive(d); setView("files"); }}
                      className={cx(
                        "flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-xs",
                        view === "files" && drive?.id === d.id ? "bg-gray-100 font-medium text-gray-900" : "text-gray-600 hover:bg-gray-50",
                      )}
                    >
                      <HardDrive size={13} /> <span className="truncate">{d.name}</span>
                    </button>
                  ))}
                  <button
                    type="button"
                    onClick={() => setView("lists")}
                    className={cx(
                      "flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-xs",
                      view === "lists" ? "bg-gray-100 font-medium text-gray-900" : "text-gray-600 hover:bg-gray-50",
                    )}
                  >
                    <ListIcon size={13} /> Lists
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        {!site && <EmptyState icon={Globe} title="Pick a site" hint="Sites you can already see in SharePoint. Blank search lists recently active ones." />}
        {site && view === "files" && drive && <DriveBrowser key={drive.id} drive={drive} />}
        {site && view === "files" && !drive && !drives.loading && <EmptyState icon={HardDrive} title="No document libraries" />}
        {site && view === "lists" && <ListsBrowser key={site.id} site={site} />}
      </section>
    </div>
  );
}

function DriveBrowser({ drive }) {
  const [path, setPath] = useState([]);
  const [searchText, setSearchText] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [notice, setNotice] = useState(null);
  const { run, busy, error, clearError } = useAction();
  const fileInput = useRef(null);
  const pathString = path.join("/");

  const items = useRequest(
    (token) =>
      search
        ? sharepointApi.search(token, drive.id, search)
        : sharepointApi.items(token, drive.id, { path: pathString || undefined, top: 50 }),
    [drive.id, pathString, search],
  );
  // The current folder's own item (for uploads/moves by id) — the root has none.
  const current = useRequest((token) => sharepointApi.itemByPath(token, drive.id, pathString), [drive.id, pathString], {
    enabled: pathString.length > 0,
  });

  const sorted = useMemo(
    () => [...(items.data || [])].sort((a, b) => (b.is_folder - a.is_folder) || (a.name || "").localeCompare(b.name || "")),
    [items.data],
  );
  const rootId = items.data?.[0]?.parent_id || null;
  const currentFolderId = pathString ? current.data?.id : rootId;

  const afterMutation = (text) => {
    setNotice(text);
    setSelected(null);
    setDialog(null);
    items.reload();
  };

  const open = (item) => {
    if (item.is_folder && !search) setPath([...path, item.name]);
    else setSelected(item);
  };

  const upload = async (file) => {
    if (!file) return;
    const created = await run("upload", (token) =>
      sharepointApi.upload(token, drive.id, file, { parentPath: pathString || undefined, conflict: "rename" }),
    );
    if (created) afterMutation(`Uploaded ${created.name}`);
  };

  const download = (item) => run("download", (token) => sharepointApi.content(token, drive.id, item.id).then(downloadBlob));

  const remove = async (item) => {
    const ok = await run("delete", (token) => sharepointApi.deleteItem(token, drive.id, item.id).then(() => true));
    if (ok) afterMutation(`${item.name} moved to the recycle bin`);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-gray-200 bg-white px-4 py-2">
        <nav className="flex min-w-0 flex-1 items-center gap-1 text-sm">
          <button type="button" onClick={() => { setPath([]); setSearch(""); setSearchText(""); }} className="flex items-center gap-1 rounded px-1.5 py-1 font-medium hover:bg-gray-100">
            <HardDrive size={14} /> {drive.name}
          </button>
          {path.map((segment, index) => (
            <span key={`${segment}-${index}`} className="flex items-center gap-1">
              <ChevronRight size={14} className="text-gray-400" />
              <button type="button" onClick={() => setPath(path.slice(0, index + 1))} className="truncate rounded px-1.5 py-1 hover:bg-gray-100">{segment}</button>
            </span>
          ))}
          {search && <Badge tone="brand" className="ml-2">search: {search}</Badge>}
        </nav>
        <form
          className="relative"
          onSubmit={(e) => {
            e.preventDefault();
            setSearch(searchText.trim());
            setSelected(null);
          }}
        >
          <Search size={14} className="pointer-events-none absolute left-2.5 top-2.5 text-gray-400" />
          <Input value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="Search this library" className="w-56 pl-8 py-1.5 text-xs" />
        </form>
        {search && <Button variant="ghost" size="sm" onClick={() => { setSearch(""); setSearchText(""); }}>Clear</Button>}
        <Button variant="ghost" size="icon" onClick={items.reload} aria-label="Refresh"><RefreshCw size={15} className={items.loading ? "animate-spin" : ""} /></Button>
        <input ref={fileInput} type="file" className="hidden" onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
        <Button variant="secondary" size="sm" onClick={() => fileInput.current?.click()} disabled={busy !== null || Boolean(search)}><Upload size={14} /> {busy === "upload" ? "Uploading…" : "Upload"}</Button>
        <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "folder" })} disabled={busy !== null || Boolean(search)}><FolderPlus size={14} /> New folder</Button>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex-1 overflow-y-auto">
          <div className="space-y-2 p-3">
            {notice && <Notice notice={notice} onDismiss={() => setNotice(null)} />}
            <ErrorBanner error={error} onDismiss={clearError} />
            <ErrorBanner error={items.error} onRetry={items.reload} />
          </div>
          {items.loading && !items.data && <Loading label="Files…" />}
          {sorted.length === 0 && items.data && <EmptyState icon={Folder} title={search ? "No matches" : "Empty folder"} />}
          {sorted.length > 0 && (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-50 text-left text-xs text-gray-500">
                <tr>
                  <th className="px-4 py-2 font-medium">Name</th>
                  <th className="w-24 px-2 py-2 font-medium">Size</th>
                  <th className="w-44 px-2 py-2 font-medium">Modified</th>
                  <th className="w-36 px-2 py-2 font-medium">By</th>
                  <th className="w-32 px-2 py-2" />
                </tr>
              </thead>
              <tbody>
                {sorted.map((item) => (
                  <tr key={item.id} className={cx("border-t border-gray-100 hover:bg-gray-50", selected?.id === item.id && "bg-brand-dim")}>
                    <td className="px-4 py-2">
                      <button type="button" onClick={() => open(item)} className="flex items-center gap-2 text-left">
                        {item.is_folder ? <Folder size={16} className="text-amber-500" /> : isTextType(item.mime_type) ? <FileText size={16} className="text-gray-500" /> : <FileIcon size={16} className="text-gray-500" />}
                        <span className="truncate">{item.name}</span>
                        {item.is_folder && item.child_count !== null && <span className="text-xs text-gray-400">{item.child_count}</span>}
                      </button>
                    </td>
                    <td className="px-2 py-2 text-xs text-gray-500">{item.is_folder ? "" : formatBytes(item.size)}</td>
                    <td className="px-2 py-2 text-xs text-gray-500">{formatDateTime(item.last_modified_at)}</td>
                    <td className="truncate px-2 py-2 text-xs text-gray-500">{item.last_modified_by?.display_name || ""}</td>
                    <td className="px-2 py-2">
                      <div className="flex items-center justify-end gap-0.5">
                        {!item.is_folder && <Button variant="ghost" size="icon" title="Download" aria-label="Download" onClick={() => download(item)} disabled={busy !== null}><Download size={14} /></Button>}
                        <Button variant="ghost" size="icon" title="Rename" aria-label="Rename" onClick={() => setDialog({ kind: "rename", item })} disabled={busy !== null}><Pencil size={14} /></Button>
                        <Button variant="ghost" size="icon" title="Move" aria-label="Move" onClick={() => setDialog({ kind: "move", item })} disabled={busy !== null}><FolderSymlink size={14} /></Button>
                        <Button variant="ghost" size="icon" title="Delete" aria-label="Delete" onClick={() => setDialog({ kind: "delete", item })} disabled={busy !== null}><Trash2 size={14} /></Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {selected && !selected.is_folder && (
          <FilePreview key={selected.id} drive={drive} item={selected} onClose={() => setSelected(null)} />
        )}
      </div>

      {dialog?.kind === "folder" && (
        <NameDialog
          title="New folder"
          label="Folder name"
          busy={busy === "folder"}
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            const created = await run("folder", (token) =>
              sharepointApi.createFolder(token, drive.id, { name, parent_path: pathString || null }),
            );
            if (created) afterMutation(`Created folder ${created.name}`);
          }}
        />
      )}
      {dialog?.kind === "rename" && (
        <NameDialog
          title={`Rename ${dialog.item.name}`}
          label="New name"
          initial={dialog.item.name}
          busy={busy === "rename"}
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            const updated = await run("rename", (token) => sharepointApi.moveItem(token, drive.id, dialog.item.id, { new_name: name }));
            if (updated) afterMutation(`Renamed to ${updated.name}`);
          }}
        />
      )}
      {dialog?.kind === "move" && (
        <MoveDialog
          item={dialog.item}
          folders={sorted.filter((i) => i.is_folder && i.id !== dialog.item.id)}
          currentFolderId={currentFolderId}
          parentFolderId={pathString ? current.data?.parent_id : null}
          rootId={rootId}
          busy={busy === "move"}
          onClose={() => setDialog(null)}
          onSubmit={async (destination) => {
            const moved = await run("move", (token) => sharepointApi.moveItem(token, drive.id, dialog.item.id, { new_parent_id: destination }));
            if (moved) afterMutation(`Moved ${moved.name}`);
          }}
        />
      )}
      {dialog?.kind === "delete" && (
        <Modal
          title={`Delete ${dialog.item.name}?`}
          onClose={() => setDialog(null)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setDialog(null)}>Keep</Button>
              <Button variant="danger" onClick={() => remove(dialog.item)} disabled={busy !== null}>{busy === "delete" ? "Deleting…" : "Move to recycle bin"}</Button>
            </>
          }
        >
          <p className="text-sm text-gray-600">It goes to the site recycle bin, where you or a site admin can restore it.</p>
        </Modal>
      )}
    </div>
  );
}

function FilePreview({ drive, item, onClose }) {
  const textual = isTextType(item.mime_type) && (item.size || 0) <= 2_000_000;
  const preview = useRequest(
    (token) => sharepointApi.content(token, drive.id, item.id).then(({ blob }) => blob.text()),
    [item.id],
    { enabled: textual },
  );
  const { run, busy } = useAction();

  return (
    <aside className="flex w-[28rem] shrink-0 flex-col border-l border-gray-200 bg-white">
      <div className="flex items-start justify-between gap-2 border-b border-gray-100 px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{item.name}</div>
          <div className="text-xs text-gray-500">{formatBytes(item.size)} · {item.mime_type || "unknown type"}</div>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close"><X size={16} /></Button>
      </div>
      <div className="flex items-center gap-2 border-b border-gray-100 px-4 py-2">
        <Button size="sm" variant="secondary" onClick={() => run("download", (token) => sharepointApi.content(token, drive.id, item.id).then(downloadBlob))} disabled={busy !== null}>
          <Download size={14} /> Download
        </Button>
        {item.web_url && (
          <a href={item.web_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-gray-500 hover:text-brand"><ExternalLink size={13} /> Open in SharePoint</a>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {!textual && <EmptyState icon={FileIcon} title="No inline preview" hint="Office documents and binaries open in SharePoint or download." />}
        {textual && preview.loading && <Loading label="Reading file…" />}
        <ErrorBanner error={preview.error} onRetry={preview.reload} className="m-3" />
        {typeof preview.data === "string" && <pre className="whitespace-pre-wrap px-4 py-3 font-mono text-xs text-gray-800">{preview.data}</pre>}
      </div>
    </aside>
  );
}

function NameDialog({ title, label, initial = "", busy, onClose, onSubmit }) {
  const [name, setName] = useState(initial);
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => onSubmit(name.trim())} disabled={busy || !name.trim() || name.includes("/")}>{busy ? "Working…" : "OK"}</Button>
        </>
      }
    >
      <Field label={label} hint="A single name — no slashes.">
        <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) onSubmit(name.trim()); }} />
      </Field>
    </Modal>
  );
}

function MoveDialog({ item, folders, currentFolderId, parentFolderId, rootId, busy, onClose, onSubmit }) {
  const options = [
    rootId && { id: rootId, label: "Library root" },
    parentFolderId && parentFolderId !== rootId && { id: parentFolderId, label: "Parent folder" },
    ...folders.map((f) => ({ id: f.id, label: f.name })),
  ].filter(Boolean).filter((o) => o.id !== currentFolderId);
  const [destination, setDestination] = useState(options[0]?.id || "");

  return (
    <Modal
      title={`Move ${item.name}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => onSubmit(destination)} disabled={busy || !destination}>{busy ? "Moving…" : "Move"}</Button>
        </>
      }
    >
      {options.length === 0 ? (
        <p className="text-sm text-gray-600">No other folder is visible from here. Open the destination's parent first.</p>
      ) : (
        <Field label="Destination" hint="Folders visible from the current location.">
          <Select value={destination} onChange={(e) => setDestination(e.target.value)}>
            {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </Select>
        </Field>
      )}
    </Modal>
  );
}

function ListsBrowser({ site }) {
  const [list, setList] = useState(null);
  const lists = useRequest((token) => sharepointApi.lists(token, site.id), [site.id]);
  const rows = useRequest((token) => sharepointApi.listItems(token, site.id, list.id), [list?.id], { enabled: Boolean(list) });

  const visible = (lists.data || []).filter((l) => !l.hidden);
  const columns = useMemo(() => {
    const keys = new Set();
    for (const row of rows.data || []) for (const key of Object.keys(row.fields || {})) keys.add(key);
    return [...keys].filter((k) => !k.startsWith("_")).slice(0, 12);
  }, [rows.data]);

  return (
    <div className="flex h-full min-h-0">
      <div className="w-64 shrink-0 overflow-y-auto border-r border-gray-200 bg-white p-2">
        {lists.loading && !lists.data && <Loading label="Lists…" />}
        <ErrorBanner error={lists.error} onRetry={lists.reload} className="m-2" />
        {visible.map((l) => (
          <button
            key={l.id}
            type="button"
            onClick={() => setList(l)}
            className={cx("flex w-full flex-col rounded-lg px-3 py-2 text-left", list?.id === l.id ? "bg-brand-dim text-brand" : "hover:bg-gray-100")}
          >
            <span className="truncate text-sm font-medium">{l.display_name || l.name}</span>
            <span className="truncate text-xs text-gray-500">{l.template}</span>
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-auto">
        {!list && <EmptyState icon={ListIcon} title="Pick a list" hint="Hidden system lists are filtered out." />}
        {list && rows.loading && !rows.data && <Loading label="Items…" />}
        <ErrorBanner error={rows.error} onRetry={rows.reload} className="m-3" />
        {list && rows.data?.length === 0 && <EmptyState icon={ListIcon} title="No items" />}
        {rows.data?.length > 0 && (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-gray-50 text-left text-xs text-gray-500">
              <tr>
                <th className="px-3 py-2 font-medium">ID</th>
                {columns.map((c) => <th key={c} className="px-3 py-2 font-medium">{c}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.data.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="px-3 py-2 text-xs text-gray-500">{row.web_url ? <a href={row.web_url} target="_blank" rel="noreferrer" className="hover:text-brand">{row.id}</a> : row.id}</td>
                  {columns.map((c) => <td key={c} className="max-w-xs truncate px-3 py-2">{formatCell(row.fields?.[c])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function formatCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
