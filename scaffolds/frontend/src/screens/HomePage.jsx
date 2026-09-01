import { useAuth } from "../auth/AuthProvider.jsx";
import { useCurrentUserOid } from "../auth/useToken.js";

// Placeholder landing screen proving the auth loop works end to end.
// Replace with your app's real entry screen; the useAuth/useToken
// wiring is the part to keep.
export default function HomePage() {
  const { user, signOut } = useAuth();
  const oid = useCurrentUserOid();

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="bg-white rounded-xl shadow-md p-8 max-w-md w-full">
        <h1 className="text-xl font-semibold mb-1">Signed in</h1>
        <p className="text-gray-500 text-sm mb-6">
          Authentication is wired up. Replace this screen with your app.
        </p>

        <dl className="text-sm mb-8 space-y-2">
          <div className="flex justify-between gap-4">
            <dt className="text-gray-500">Name</dt>
            <dd className="font-medium text-right">{user?.name || "—"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-gray-500">Username</dt>
            <dd className="font-medium text-right">{user?.username || "—"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-gray-500">Object id</dt>
            <dd className="font-mono text-xs text-right self-center">{oid || "—"}</dd>
          </div>
        </dl>

        <button
          onClick={signOut}
          className="w-full py-2.5 rounded-lg bg-brand text-white font-medium hover:bg-brand-light transition-colors"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
