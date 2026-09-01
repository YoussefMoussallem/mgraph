import { useCallback, useEffect, useRef } from "react";
import { useMsal } from "@azure/msal-react";

const DEFAULT_SCOPES = ["openid", "profile", "email"];
const DEFAULT_REFRESH_INTERVAL_MS = 45 * 60 * 1000;
const INTERACTION_REQUIRED_ERROR_CODES = new Set([
  "interaction_required",
  "login_required",
  "consent_required",
]);

function isInteractionRequired(error) {
  return INTERACTION_REQUIRED_ERROR_CODES.has(error?.errorCode);
}

/**
 * Proactively refreshes the MSAL token cache so API calls never race a
 * token that expired while the tab sat idle. Refreshes on an interval
 * (default 45 min — comfortably inside the 60-min Entra token lifetime)
 * and whenever the window regains focus or becomes visible again.
 *
 * When Entra demands interactive sign-in (Conditional Access session
 * lifetime, revocation, consent change), ``onInteractionRequired`` fires
 * instead — the caller decides whether to sign out or prompt.
 */
export function useSilentTokenRefresh({
  enabled = true,
  scopes = DEFAULT_SCOPES,
  refreshIntervalMs = DEFAULT_REFRESH_INTERVAL_MS,
  onInteractionRequired,
} = {}) {
  const { instance, accounts, inProgress } = useMsal();
  const refreshInFlightRef = useRef(null);
  const onInteractionRequiredRef = useRef(onInteractionRequired);

  useEffect(() => {
    onInteractionRequiredRef.current = onInteractionRequired;
  }, [onInteractionRequired]);

  const refresh = useCallback(async () => {
    if (!enabled || inProgress !== "none") return null;
    const account = instance.getActiveAccount() || accounts[0];
    if (!account) return null;

    if (refreshInFlightRef.current) return refreshInFlightRef.current;

    refreshInFlightRef.current = instance
      .acquireTokenSilent({ scopes, account })
      .catch((error) => {
        if (isInteractionRequired(error)) {
          onInteractionRequiredRef.current?.(error);
          return null;
        }
        console.warn("Silent token refresh failed", error);
        return null;
      })
      .finally(() => {
        refreshInFlightRef.current = null;
      });

    return refreshInFlightRef.current;
  }, [accounts, enabled, inProgress, instance, scopes]);

  useEffect(() => {
    if (!enabled) return undefined;

    refresh();
    const intervalId = window.setInterval(refresh, refreshIntervalMs);

    const refreshWhenActive = () => {
      if (document.visibilityState === "visible") refresh();
    };

    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refreshWhenActive);

    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refreshWhenActive);
    };
  }, [enabled, refresh, refreshIntervalMs]);
}
