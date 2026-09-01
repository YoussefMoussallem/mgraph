import { NavLink } from "react-router-dom";
import { CalendarDays, FolderOpen, LogOut, Mail, Sparkles, Users } from "lucide-react";
import { useAuth } from "../auth/AuthProvider.jsx";
import { initials } from "../lib/format.js";
import { cx } from "./ui.jsx";

const NAV = [
  { to: "/mail", label: "Mail", icon: Mail },
  { to: "/calendar", label: "Calendar", icon: CalendarDays },
  { to: "/contacts", label: "Contacts", icon: Users },
  { to: "/files", label: "Files", icon: FolderOpen },
  { to: "/assistant", label: "Assistant", icon: Sparkles },
];

export default function Layout({ children }) {
  const { user, signOut } = useAuth();

  return (
    <div className="flex h-screen bg-gray-50 text-gray-900">
      <aside className="flex w-60 shrink-0 flex-col border-r border-gray-200 bg-white">
        <div className="border-b border-gray-100 px-5 py-5">
          <div className="font-heading text-lg font-bold text-brand">M365 Workspace</div>
          <div className="text-xs text-gray-500">Outlook · SharePoint · Assistant</div>
        </div>

        <nav className="flex-1 space-y-1 p-3">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cx(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  isActive ? "bg-brand-dim text-brand" : "text-gray-700 hover:bg-gray-100",
                )
              }
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-gray-100 p-3">
          <div className="flex items-center gap-3 px-2 py-1">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand text-xs font-semibold text-white">
              {initials(user?.name)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{user?.name || "Signed in"}</div>
              <div className="truncate text-xs text-gray-500">{user?.username || ""}</div>
            </div>
            <button
              type="button"
              onClick={signOut}
              title="Sign out"
              aria-label="Sign out"
              className="rounded-md p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-800"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">{children}</main>
    </div>
  );
}
