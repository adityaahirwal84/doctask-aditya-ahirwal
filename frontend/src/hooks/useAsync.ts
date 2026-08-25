import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

/**
 * Every page's data loading goes through this one hook, so "loading
 * spinner while fetching, readable error banner with a retry option if it
 * fails" is a property of the app, not something re-implemented (or
 * forgotten) per page.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

  const load = useCallback(() => {
    const myGeneration = ++generation.current;
    setLoading(true);
    setError(null);
    fn()
      .then((result) => {
        if (myGeneration === generation.current) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (myGeneration === generation.current) {
          setError(err instanceof ApiError ? err.message : "Something went wrong.");
          setLoading(false);
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    load();
  }, [load]);

  return { data, loading, error, refetch: load };
}

/** Polling variant for run status / pending approvals, which change on
 * their own as the worker processes a run - the review UI needs to
 * reflect that without the reviewer manually refreshing. */
export function usePolling<T>(fn: () => Promise<T>, deps: unknown[], intervalMs = 2500): AsyncState<T> {
  const state = useAsync(fn, deps);
  useEffect(() => {
    const id = setInterval(() => state.refetch(), intervalMs);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export interface MutationState<TArgs extends unknown[], TResult> {
  run: (...args: TArgs) => Promise<TResult | undefined>;
  pending: boolean;
  error: string | null;
  clearError: () => void;
}

/** Same idea for write actions (approve/reject, upload, start run): a
 * consistent pending flag + error surface, with the error clearable so a
 * banner doesn't linger after the user has acted on it. */
export function useMutation<TArgs extends unknown[], TResult>(
  fn: (...args: TArgs) => Promise<TResult>
): MutationState<TArgs, TResult> {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(
    async (...args: TArgs) => {
      setPending(true);
      setError(null);
      try {
        const result = await fn(...args);
        setPending(false);
        return result;
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Something went wrong.");
        setPending(false);
        return undefined;
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fn]
  );

  return { run, pending, error, clearError: () => setError(null) };
}
