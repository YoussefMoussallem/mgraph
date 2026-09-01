import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { AlertTriangle, Bot, ChevronDown, ChevronRight, Send, Sparkles, Trash2, User, Wrench } from "lucide-react";
import { agentApi } from "../api/agent.js";
import { Badge, Button, ErrorBanner, Loading, PageHeader, Textarea, Toggle, cx } from "../components/ui.jsx";
import { useApiCall, useRequest } from "../hooks/useApi.js";

const SUGGESTIONS = [
  "What's new in my inbox today?",
  "What's on my calendar this week?",
  "Find the latest budget file in SharePoint and summarise it",
  "Draft a reply to the most recent email from my manager",
];

export default function AssistantPage() {
  const call = useApiCall();
  const status = useRequest((token) => agentApi.status(token), []);
  const [includeWrites, setIncludeWrites] = useState(false);
  const palette = useRequest((token) => agentApi.tools(token, { includeWrites }), [includeWrites]);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const bottom = useRef(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const send = async (text) => {
    const content = (text ?? input).trim();
    if (!content || busy) return;
    const next = [...messages, { role: "user", content }];
    setMessages(next);
    setInput("");
    setBusy(true);
    setError(null);
    try {
      const result = await call((token) =>
        agentApi.chat(token, {
          messages: next.map(({ role, content: c }) => ({ role, content: c })),
          includeWrites,
        }),
      );
      setMessages([...next, { role: "assistant", content: result.reply, steps: result.steps }]);
    } catch (err) {
      setError(err);
      setMessages(messages);
      setInput(content);
    } finally {
      setBusy(false);
    }
  };

  const configured = status.data?.configured;

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader title="Assistant" subtitle="Works as you, through the same tools an Agent Studio agent would get">
          <Toggle
            checked={includeWrites}
            onChange={setIncludeWrites}
            label="Allow actions"
            description={includeWrites ? "Can send mail, create events, upload and move files" : "Read-only: 13 tools"}
          />
          <Button variant="ghost" size="icon" title="Clear conversation" aria-label="Clear conversation" onClick={() => setMessages([])} disabled={busy || messages.length === 0}>
            <Trash2 size={16} />
          </Button>
        </PageHeader>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {status.loading && !status.data && <Loading label="Checking the assistant…" />}
          {status.data && !configured && (
            <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <span>No model is configured. Set <code>OPENAI_API_KEY</code> and <code>AGENT_MODEL</code> (and <code>OPENAI_BASE_URL</code> for a proxy) in the backend environment to enable the assistant. Mail, calendar and files work without it.</span>
            </div>
          )}

          {messages.length === 0 && (
            <div className="mx-auto mt-10 max-w-xl text-center">
              <Sparkles size={36} className="mx-auto text-brand" />
              <h2 className="mt-3 text-lg font-semibold">Ask about your mailbox, calendar or files</h2>
              <p className="mt-1 text-sm text-gray-500">
                The assistant only sees what you can see. Turn on <em>Allow actions</em> to let it send, schedule and upload — it will still ask before anything irreversible unless you told it exactly what to do.
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button key={s} type="button" onClick={() => send(s)} disabled={!configured || busy} className="rounded-full border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-700 hover:border-brand/40 hover:text-brand disabled:opacity-50">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="mx-auto max-w-3xl space-y-4">
            {messages.map((m, index) => (
              <ChatBubble key={index} message={m} />
            ))}
            {busy && (
              <div className="flex items-center gap-2 text-sm text-gray-500">
                <Bot size={16} className="text-brand" /> Working… tool calls appear below the reply.
              </div>
            )}
            {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}
            <div ref={bottom} />
          </div>
        </div>

        <form
          className="border-t border-gray-200 bg-white px-6 py-3"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={configured ? "Ask something… (Enter to send, Shift+Enter for a new line)" : "Configure a model to chat"}
              disabled={!configured || busy}
              className="min-h-12 max-h-40"
              rows={1}
            />
            <Button type="submit" disabled={!configured || busy || !input.trim()} aria-label="Send">
              <Send size={16} />
            </Button>
          </div>
        </form>
      </div>

      <aside className="hidden w-80 shrink-0 flex-col border-l border-gray-200 bg-white xl:flex">
        <div className="border-b border-gray-100 px-4 py-3">
          <div className="text-sm font-semibold">Tools bound to this chat</div>
          <div className="text-xs text-gray-500">{palette.data ? `${palette.data.length} tools` : "…"} · from m365-langchain-tools</div>
        </div>
        <div className="flex-1 overflow-y-auto p-3">
          {palette.loading && !palette.data && <Loading label="Tools…" />}
          <ErrorBanner error={palette.error} onRetry={palette.reload} />
          <ul className="space-y-1">
            {palette.data?.map((tool) => (
              <li key={tool.name} className="rounded-lg px-2 py-1.5 hover:bg-gray-50" title={tool.description}>
                <div className="flex items-center gap-2">
                  <Wrench size={12} className="shrink-0 text-gray-400" />
                  <span className="truncate font-mono text-xs">{tool.name}</span>
                  <Badge tone={tool.write ? "amber" : "green"} className="ml-auto">{tool.write ? "write" : "read"}</Badge>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}

function ChatBubble({ message }) {
  const isUser = message.role === "user";
  return (
    <div className={cx("flex gap-3", isUser && "flex-row-reverse")}>
      <div className={cx("flex h-8 w-8 shrink-0 items-center justify-center rounded-full", isUser ? "bg-gray-200 text-gray-700" : "bg-brand text-white")}>
        {isUser ? <User size={16} /> : <Bot size={16} />}
      </div>
      <div className={cx("max-w-[85%] space-y-2", isUser && "text-right")}>
        <div className={cx("inline-block rounded-2xl px-4 py-2.5 text-left text-sm", isUser ? "bg-brand text-white" : "bg-white border border-gray-200 text-gray-900")}>
          {isUser ? <span className="whitespace-pre-wrap">{message.content}</span> : <div className="prose-chat"><ReactMarkdown>{message.content}</ReactMarkdown></div>}
        </div>
        {message.steps?.length > 0 && (
          <div className="space-y-1 text-left">
            {message.steps.map((step, index) => (
              <ToolStep key={index} step={step} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolStep({ step }) {
  const [open, setOpen] = useState(false);
  const failed = /^(Error|Not found|Conflict|Microsoft Graph)/.test(step.result || "");
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 text-xs">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-1.5 text-left">
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Wrench size={12} className="text-gray-400" />
        <span className="font-mono">{step.tool}</span>
        <span className="truncate text-gray-500">{summarizeArgs(step.args)}</span>
        {failed && <Badge tone="red" className="ml-auto">error</Badge>}
      </button>
      {open && (
        <div className="space-y-2 border-t border-gray-200 px-3 py-2">
          <div>
            <div className="font-medium text-gray-500">Arguments</div>
            <pre className="whitespace-pre-wrap font-mono text-[11px]">{JSON.stringify(step.args, null, 2)}</pre>
          </div>
          <div>
            <div className="font-medium text-gray-500">Result</div>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{prettyResult(step.result)}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

function summarizeArgs(args) {
  const entries = Object.entries(args || {});
  if (entries.length === 0) return "";
  return entries.map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");
}

function prettyResult(result) {
  try {
    return JSON.stringify(JSON.parse(result), null, 2);
  } catch {
    return result;
  }
}
