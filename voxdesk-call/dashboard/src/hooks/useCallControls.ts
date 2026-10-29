/**
 * dashboard/src/hooks/useCallControls.ts
 *
 * React Query mutations for the six live call-control actions.
 *
 * Two rules shape everything here:
 *
 * 1. **No optimistic updates.** A call control action is a side effect on a
 *    live carrier call. Showing "paused" before the server confirms it would
 *    tell an operator the caller is on hold while the AI is still listening,
 *    and there is no way to un-hear that. Every mutation waits for the
 *    response and then invalidates the call detail query.
 * 2. **Errors keep their server code.** `{ code, message }` is preserved from
 *    the API's `detail` body rather than flattened into a string, so the UI
 *    can distinguish "this provider cannot pause" (501
 *    `UNSUPPORTED_CAPABILITY`) from "you must resume first" (409).
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  bulkTransfer,
  cancelTransfer,
  pauseCall,
  resumeCall,
  sendDtmfBatch,
  whisperTransfer,
} from '../api/calls';
import type {
  BulkTransferItem,
  BulkTransferResponse,
  CallPauseResponse,
  CallResumeResponse,
  DtmfBatchResponse,
  DtmfItem,
  TransferCancelResponse,
  TransferWhisperResponse,
} from '../api/calls';

/** The `{ code, message }` body the API returns on every control failure. */
export interface CallControlError {
  code: string;
  message: string;
  status: number;
}

export interface CallControlFailure {
  error: CallControlError;
}

export function callDetailQueryKey(callId: string): readonly unknown[] {
  return ['call', callId] as const;
}

/**
 * Normalise whatever the transport threw into `{ code, message }`.
 *
 * `apiClient` already folds FastAPI's `detail` object into `ApiError.detail`,
 * so the server's own code is used when present. Anything else keeps its
 * shape but is still typed, because a control failure the UI cannot classify
 * is a failure the operator cannot act on.
 */
export function toControlError(err: unknown): CallControlError {
  const anyErr = err as {
    status?: number;
    detail?: unknown;
    message?: string;
  } | null;
  const status = typeof anyErr?.status === 'number' ? anyErr.status : 0;
  const detail = anyErr?.detail;

  if (detail && typeof detail === 'object') {
    const record = detail as { code?: unknown; message?: unknown };
    return {
      code: typeof record.code === 'string' ? record.code : 'unknown_error',
      message:
        typeof record.message === 'string'
          ? record.message
          : typeof anyErr?.message === 'string'
            ? anyErr.message
            : 'The call control action failed.',
      status,
    };
  }
  return {
    code: status === 501 ? 'UNSUPPORTED_CAPABILITY' : 'unknown_error',
    message:
      (typeof detail === 'string' && detail) ||
      (typeof anyErr?.message === 'string' && anyErr.message) ||
      'The call control action failed.',
    status,
  };
}

export interface UseCallControlsOptions {
  callId: string;
  /** Extra query keys to invalidate once a control action succeeds. */
  invalidateKeys?: readonly (readonly unknown[])[];
}

export interface CallControlMutation<TVariables, TData> {
  mutate: (variables: TVariables) => void;
  mutateAsync: (variables: TVariables) => Promise<TData>;
  isPending: boolean;
  isError: boolean;
  isSuccess: boolean;
  data: TData | undefined;
  error: CallControlError | null;
  reset: () => void;
}

function useControlMutation<TVariables, TData>(options: {
  callId: string;
  invalidateKeys?: readonly (readonly unknown[])[];
  mutationFn: (variables: TVariables) => Promise<TData>;
}): CallControlMutation<TVariables, TData> {
  const { callId, invalidateKeys, mutationFn } = options;
  const queryClient = useQueryClient();

  const mutation = useMutation<TData, unknown, TVariables>({
    mutationFn,
    // A control action changes the call row, the call's events and its
    // transfer state, all of which the detail view reads. Invalidating on
    // settlement (not just success) is deliberate: a rejected action still
    // needs a refetch, because a 409 means our local state is stale.
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: callDetailQueryKey(callId) });
      for (const key of invalidateKeys ?? []) {
        await queryClient.invalidateQueries({ queryKey: key as unknown[] });
      }
    },
  });

  return {
    mutate: mutation.mutate,
    mutateAsync: mutation.mutateAsync,
    isPending: mutation.isPending,
    isError: mutation.isError,
    isSuccess: mutation.isSuccess,
    data: mutation.data,
    error: mutation.isError ? toControlError(mutation.error) : null,
    reset: mutation.reset,
  };
}

export interface PauseVariables {
  reason?: string;
  holdMessage?: string;
  idempotencyKey?: string;
}

export interface ResumeVariables {
  reason?: string;
  idempotencyKey?: string;
}

export interface DtmfVariables {
  items: DtmfItem[];
  idempotencyKey?: string;
}

export interface CancelTransferVariables {
  reason?: string;
  idempotencyKey?: string;
}

export interface WhisperVariables {
  message: string;
  idempotencyKey?: string;
}

export interface BulkTransferVariables {
  items: BulkTransferItem[];
  idempotencyKey?: string;
}

export interface UseCallControlsResult {
  pause: CallControlMutation<PauseVariables, CallPauseResponse>;
  resume: CallControlMutation<ResumeVariables, CallResumeResponse>;
  sendDtmf: CallControlMutation<DtmfVariables, DtmfBatchResponse>;
  cancelTransfer: CallControlMutation<
    CancelTransferVariables,
    TransferCancelResponse
  >;
  whisper: CallControlMutation<WhisperVariables, TransferWhisperResponse>;
  bulkTransfer: CallControlMutation<BulkTransferVariables, BulkTransferResponse>;
  /** True while any of the six actions is in flight. */
  isBusy: boolean;
  /** The most recent failure, or null. */
  lastError: CallControlError | null;
}

export function useCallControls(
  options: UseCallControlsOptions
): UseCallControlsResult {
  const shared = {
    callId: options.callId,
    invalidateKeys: options.invalidateKeys,
  };

  const pause = useControlMutation<PauseVariables, CallPauseResponse>({
    ...shared,
    mutationFn: (vars) =>
      pauseCall(
        options.callId,
        { reason: vars.reason, hold_message: vars.holdMessage },
        vars.idempotencyKey
      ),
  });

  const resume = useControlMutation<ResumeVariables, CallResumeResponse>({
    ...shared,
    mutationFn: (vars) =>
      resumeCall(options.callId, { reason: vars.reason }, vars.idempotencyKey),
  });

  const sendDtmf = useControlMutation<DtmfVariables, DtmfBatchResponse>({
    ...shared,
    mutationFn: (vars) =>
      sendDtmfBatch(options.callId, vars.items, vars.idempotencyKey),
  });

  const cancel = useControlMutation<
    CancelTransferVariables,
    TransferCancelResponse
  >({
    ...shared,
    mutationFn: (vars) =>
      cancelTransfer(options.callId, { reason: vars.reason }, vars.idempotencyKey),
  });

  const whisper = useControlMutation<WhisperVariables, TransferWhisperResponse>({
    ...shared,
    mutationFn: (vars) =>
      whisperTransfer(options.callId, vars.message, vars.idempotencyKey),
  });

  const bulk = useControlMutation<BulkTransferVariables, BulkTransferResponse>({
    ...shared,
    mutationFn: (vars) => bulkTransfer(vars.items, vars.idempotencyKey),
  });

  const all = [pause, resume, sendDtmf, cancel, whisper, bulk];
  const lastError =
    [...all].reverse().find((m) => m.error !== null)?.error ?? null;

  return {
    pause,
    resume,
    sendDtmf,
    cancelTransfer: cancel,
    whisper,
    bulkTransfer: bulk,
    isBusy: all.some((m) => m.isPending),
    lastError,
  };
}

export default useCallControls;
