import { useCallback, useEffect, useRef, useState } from "react";
import { useToken } from "../auth/useToken.js";

/**
 * ``const call = useApiCall(); await call((token) => outlookApi.send(token, body))``
 *
 * Gets a fresh access token and hands it to the API function. A missing
 * token means the session died — ``useToken`` is already redirecting or
 * signing out — so the call is abandoned with an error the UI can show.
 */
export function useApiCall() {
  const getToken = useToken();
  return useCallback(
    async (fn) => {
      const token = await getToken();
      if (!token) throw new Error("Not signed in");
      return fn(token);
    },
    [getToken],
  );
}

/**
 * Declarative loader for read endpoints.
 *
 *   const folders = useRequest((token) => outlookApi.folders(token), []);
 *   folders.data / folders.loading / folders.error / folders.reload()
 *
 * Re-runs when ``deps`` change; ``enabled: false`` parks it with no data
 * (for "nothing selected yet"). ``setData`` lets a mutation update the
 * cached result in place instead of refetching.
 */
export function useRequest(loader, deps, { enabled = true } = {}) {
  const call = useApiCall();
  const [state, setState] = useState({ data: null, loading: enabled, error: null });
  const [tick, setTick] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, loading: false, error: null });
      return undefined;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    call((token) => loaderRef.current(token))
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error) => {
        if (!cancelled) setState((s) => ({ ...s, loading: false, error }));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [call, enabled, tick, ...deps]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setData = useCallback(
    (updater) =>
      setState((s) => ({ ...s, data: typeof updater === "function" ? updater(s.data) : updater })),
    [],
  );

  return { ...state, reload, setData };
}

/**
 * Tracks one in-flight mutation: ``const { run, busy, error } = useAction()``.
 * ``run(label, fn)`` sets ``busy`` to the label while ``fn`` runs and keeps
 * the last error for the UI to show.
 */
export function useAction() {
  const call = useApiCall();
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const run = useCallback(
    async (label, fn) => {
      setBusy(label);
      setError(null);
      try {
        return await call(fn);
      } catch (err) {
        setError(err);
        return undefined;
      } finally {
        setBusy(null);
      }
    },
    [call],
  );

  return { run, busy, error, clearError: () => setError(null) };
}
