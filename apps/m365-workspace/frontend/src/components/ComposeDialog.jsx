import { useRef, useState } from "react";
import { Paperclip, Save, Send } from "lucide-react";
import { outlookApi } from "../api/outlook.js";
import { useAction } from "../hooks/useApi.js";
import { formatBytes } from "../lib/format.js";
import { Badge, Button, ErrorBanner, Field, Input, Modal, Textarea, Toggle } from "./ui.jsx";

const splitAddresses = (text) =>
  text
    .split(/[,;\s]+/)
    .map((part) => part.trim())
    .filter(Boolean);

const TITLES = {
  new: "New message",
  reply: "Reply",
  replyAll: "Reply all",
  forward: "Forward",
};

/**
 * Compose a new message, reply/reply-all, or forward.
 *
 * New messages can be sent straight away (``sendMail``) or saved as a
 * draft first; a saved draft accepts attachments (Graph's 3 MB
 * single-request limit applies) and is then sent with ``send_draft``.
 * Replies and forwards let Graph compose the quoted original and only take
 * a comment (plus recipients for a forward).
 */
export default function ComposeDialog({ mode = "new", message = null, onClose, onDone }) {
  const isReply = mode === "reply" || mode === "replyAll";
  const isForward = mode === "forward";
  const { run, busy, error } = useAction();
  const fileInput = useRef(null);

  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [bcc, setBcc] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [html, setHtml] = useState(false);
  const [draft, setDraft] = useState(null);
  const [attachments, setAttachments] = useState([]);

  const composeBody = () => ({
    to: splitAddresses(to),
    cc: splitAddresses(cc),
    bcc: splitAddresses(bcc),
    subject,
    body,
    body_type: html ? "html" : "text",
  });

  const send = async () => {
    const ok = await run("send", async (token) => {
      if (isReply) {
        await outlookApi.reply(token, message.id, { comment: body, reply_all: mode === "replyAll" });
        return "Reply sent";
      }
      if (isForward) {
        await outlookApi.forward(token, message.id, { to: splitAddresses(to), comment: body });
        return "Message forwarded";
      }
      if (draft) {
        await outlookApi.sendDraft(token, draft.id);
        return "Draft sent";
      }
      await outlookApi.send(token, composeBody());
      return "Message sent";
    });
    if (ok) onDone(ok);
  };

  const saveDraft = async () => {
    const created = await run("draft", (token) => outlookApi.createDraft(token, composeBody()));
    if (created) setDraft(created);
  };

  const attach = async (file) => {
    if (!file || !draft) return;
    const added = await run("attach", (token) => outlookApi.addAttachment(token, draft.id, file));
    if (added) setAttachments((list) => [...list, added]);
  };

  const canSend = isReply || isForward ? (isForward ? splitAddresses(to).length > 0 : true) : Boolean(draft) || (splitAddresses(to).length > 0 && subject.trim());

  return (
    <Modal
      title={TITLES[mode]}
      onClose={onClose}
      wide
      footer={
        <>
          {mode === "new" && !draft && (
            <Button variant="secondary" onClick={saveDraft} disabled={busy !== null || !subject.trim()}>
              <Save size={16} /> {busy === "draft" ? "Saving…" : "Save as draft"}
            </Button>
          )}
          {draft && (
            <Button variant="secondary" onClick={() => fileInput.current?.click()} disabled={busy !== null}>
              <Paperclip size={16} /> {busy === "attach" ? "Attaching…" : "Attach file"}
            </Button>
          )}
          <Button onClick={send} disabled={busy !== null || !canSend}>
            <Send size={16} /> {busy === "send" ? "Sending…" : draft ? "Send draft" : "Send"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <ErrorBanner error={error} />

        {(isReply || isForward) && message && (
          <div className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600">
            {isReply ? "Replying to" : "Forwarding"} <span className="font-medium text-gray-800">{message.subject || "(no subject)"}</span>
            {" — "}from {message.from_name || message.from_address}. Outlook adds the quoted original.
          </div>
        )}

        {(mode === "new" || isForward) && (
          <Field label="To" hint="Separate several addresses with commas.">
            <Input value={to} onChange={(e) => setTo(e.target.value)} placeholder="name@company.com" disabled={Boolean(draft)} autoFocus />
          </Field>
        )}
        {mode === "new" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Cc">
              <Input value={cc} onChange={(e) => setCc(e.target.value)} disabled={Boolean(draft)} />
            </Field>
            <Field label="Bcc">
              <Input value={bcc} onChange={(e) => setBcc(e.target.value)} disabled={Boolean(draft)} />
            </Field>
          </div>
        )}
        {mode === "new" && (
          <Field label="Subject">
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} disabled={Boolean(draft)} />
          </Field>
        )}
        <Field label={isReply || isForward ? "Your message" : "Body"}>
          <Textarea value={body} onChange={(e) => setBody(e.target.value)} disabled={Boolean(draft)} className="min-h-40" />
        </Field>
        {mode === "new" && !draft && (
          <Toggle checked={html} onChange={setHtml} label="Body is HTML" description="Off: plain text." />
        )}

        {draft && (
          <div className="space-y-2 rounded-lg border border-gray-200 p-3">
            <div className="flex items-center gap-2 text-xs text-gray-600">
              <Badge tone="brand">Draft saved</Badge>
              <span>Attach files, then send. The draft is in your Drafts folder.</span>
            </div>
            <input
              ref={fileInput}
              type="file"
              className="hidden"
              onChange={(e) => {
                attach(e.target.files?.[0]);
                e.target.value = "";
              }}
            />
            {attachments.length > 0 && (
              <ul className="space-y-1 text-sm">
                {attachments.map((a) => (
                  <li key={a.id} className="flex items-center gap-2">
                    <Paperclip size={14} className="text-gray-400" />
                    <span>{a.name}</span>
                    <span className="text-xs text-gray-400">{formatBytes(a.size)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
