import { useEffect, useState } from "react";
import { Briefcase, Mail, Phone, Search, Users } from "lucide-react";
import { outlookApi } from "../api/outlook.js";
import { EmptyState, ErrorBanner, Input, Loading, PageHeader } from "../components/ui.jsx";
import { useRequest } from "../hooks/useApi.js";
import { initials } from "../lib/format.js";

export default function ContactsPage() {
  const [text, setText] = useState("");
  const [prefix, setPrefix] = useState("");

  // Debounced prefix search: the SDK filters with startswith(displayName, …).
  useEffect(() => {
    const handle = setTimeout(() => setPrefix(text.trim()), 300);
    return () => clearTimeout(handle);
  }, [text]);

  const contacts = useRequest(
    (token) => outlookApi.contacts(token, { name_starts_with: prefix || undefined, top: 50 }),
    [prefix],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Contacts" subtitle="Your personal contacts">
        <div className="relative w-72">
          <Search size={16} className="pointer-events-none absolute left-3 top-2.5 text-gray-400" />
          <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Name starts with…" className="pl-9" />
        </div>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-6">
        <ErrorBanner error={contacts.error} onRetry={contacts.reload} className="mb-4" />
        {contacts.loading && !contacts.data && <Loading label="Contacts…" />}
        {contacts.data?.length === 0 && <EmptyState icon={Users} title="No contacts" hint={prefix ? `Nobody whose name starts with “${prefix}”.` : "Your contacts folder is empty."} />}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {contacts.data?.map((c) => (
            <div key={c.id} className="flex gap-3 rounded-xl border border-gray-200 bg-white p-4">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-dim text-sm font-semibold text-brand">
                {initials(c.display_name)}
              </div>
              <div className="min-w-0 flex-1 space-y-1 text-sm">
                <div className="truncate font-medium">{c.display_name || "(no name)"}</div>
                {(c.job_title || c.company_name) && (
                  <div className="flex items-center gap-1.5 truncate text-xs text-gray-500">
                    <Briefcase size={12} /> {[c.job_title, c.company_name].filter(Boolean).join(" · ")}
                  </div>
                )}
                {c.email_addresses?.map((address) => (
                  <a key={address} href={`mailto:${address}`} className="flex items-center gap-1.5 truncate text-xs text-gray-600 hover:text-brand">
                    <Mail size={12} /> {address}
                  </a>
                ))}
                {[c.mobile_phone, ...(c.business_phones || [])].filter(Boolean).map((phone) => (
                  <div key={phone} className="flex items-center gap-1.5 text-xs text-gray-600">
                    <Phone size={12} /> {phone}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
