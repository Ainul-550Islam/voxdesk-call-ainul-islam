"use client";

/**
 * One data-loading hook for the whole console.
 *
 * It keeps the three states every panel must be able to draw — loading, failed,
 * loaded — in one place, and it never fabricates an empty result on failure: a panel
 * that shows "0 domains" because the request failed is worse than one that shows the
 * error, because the operator concludes the tenant has nothing configured.
 *
 * `reload` is stable, so a button can call it directly.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

export function useApi<T>(loader: () => Promise<T>, deps: readonly unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const reload = useCallback(() => {
    setNonce((value) => value + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loader()
      .then((result) => {
        if (cancelled || !mounted.current) return;
        setData(result);
      })
      .catch((cause: unknown) => {
        if (cancelled || !mounted.current) return;
        setError(cause instanceof Error ? cause : new Error(String(cause)));
        setData(null);
      })
      .finally(() => {
        if (cancelled || !mounted.current) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps are the caller's contract
  }, [nonce, ...deps]);

  return { data, error, loading, reload };
}

export default useApi;
