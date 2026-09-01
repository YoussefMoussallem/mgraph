// Renders an HTML email body in a sandboxed iframe: no scripts, no forms,
// no same-origin access — the message can style itself but cannot touch
// the app, its storage or its token. ``srcDoc`` avoids a network round
// trip and keeps the document off any URL.
export default function HtmlBody({ html, className }) {
  const doc = `<!doctype html><html><head><meta charset="utf-8">
<base target="_blank">
<style>
  body { margin: 0; padding: 4px; font: 14px/1.5 "Funnel Sans", system-ui, sans-serif; color: #111; word-break: break-word; }
  img { max-width: 100%; height: auto; }
  blockquote { border-left: 3px solid #ddd; margin: 8px 0; padding-left: 12px; color: #555; }
</style></head><body>${html}</body></html>`;

  return (
    <iframe
      title="Message body"
      sandbox=""
      srcDoc={doc}
      className={className || "h-full min-h-[50vh] w-full border-0 bg-white"}
    />
  );
}
