/** Employee-facing workforce calls: the published rota, time off and swaps.
 *
 * Every call here is self-scoped: the API takes the person from the session, never
 * from the request body, so there is no `userId` parameter to pass — and no way for a
 * caller to ask for somebody else's rota. `user_id` only appears in a request when an
 * approver files on someone's behalf, which is a supervisor flow, not this SDK.
 *
 * Paths and field names match `app/platform_v2/wfm/router.py` exactly:
 *
 * | Call | Route |
 * | --- | --- |
 * | `myShifts()` | `GET /api/wfm/my-shifts?start=&end=` |
 * | `myTimeOff()` | `GET /api/wfm/time-off?statuses=` |
 * | `balance()` | `GET /api/wfm/time-off/balances` |
 * | `requestTimeOff()` | `POST /api/wfm/time-off` |
 * | `cancelTimeOff()` | `POST /api/wfm/time-off/{id}/cancel` |
 * | `mySwaps()` | `GET /api/wfm/swaps` |
 * | `offerSwap()` | `POST /api/wfm/swaps` |
 * | `acceptSwap()` | `POST /api/wfm/swaps/{id}/accept` |
 */

import type { VoxDeskClient } from "./client.js";

/** One exported shift, in the schedule's zone (`paid_hours` is net of unpaid breaks). */
export interface Shift {
  id: string;
  starts_at: string;
  ends_at: string;
  day: string;
  paid_hours: number;
  break_minutes: number;
  status: string;
  skill_key: string;
  timezone: string;
}

export interface ShiftsResponse {
  user_id: string;
  shifts: Shift[];
  total_paid_hours: number;
}

export type TimeOffStatus = "requested" | "approved" | "denied" | "cancelled";
export type TimeOffKind = "vacation" | "sick" | "unpaid" | "training" | "bereavement";

export interface TimeOffRequest {
  id: string;
  user_id: string;
  kind: TimeOffKind;
  starts_at: string;
  ends_at: string;
  hours: number;
  status: TimeOffStatus;
  reason: string;
  /** The coverage check the approver was shown: `{required, scheduled, projected, ok}`. */
  coverage: Record<string, unknown>;
  decided_at: string | null;
}

export interface TimeOffBalance {
  kind: string;
  balance_hours: number;
  accrued_hours: number;
  used_hours: number;
  as_of: string | null;
  allows_negative: boolean;
}

export interface SwapOffer {
  id: string;
  shift_id: string;
  offering_user_id: string;
  target_user_id: string;
  status: string;
  rule_check: Record<string, unknown>;
  note: string;
}

export class WfmApi {
  constructor(private readonly client: VoxDeskClient) {}

  /** The caller's own rota. The API defaults the window to "yesterday onward". */
  async myShifts(options: { start?: string; end?: string } = {}): Promise<ShiftsResponse> {
    const query: Record<string, string | undefined> = {};
    if (options.start) query.start = options.start;
    if (options.end) query.end = options.end;
    return this.client.request<ShiftsResponse>("/api/wfm/my-shifts", { query });
  }

  async myTimeOff(statuses: TimeOffStatus[] = ["requested"]): Promise<TimeOffRequest[]> {
    const response = await this.client.request<{ requests: TimeOffRequest[] }>(
      "/api/wfm/time-off",
      { query: { statuses: statuses.join(",") } },
    );
    return response.requests;
  }

  async balances(): Promise<TimeOffBalance[]> {
    const response = await this.client.request<{ user_id: string; balances: TimeOffBalance[] }>(
      "/api/wfm/time-off/balances",
    );
    return response.balances;
  }

  /**
   * Submit a request. `idempotencyKey` is required, not optional: a phone on a train
   * retries, and without a key that is two requests and possibly two manager
   * notifications for one absence.
   */
  async requestTimeOff(input: {
    startsAt: string;
    endsAt: string;
    kind?: TimeOffKind;
    reason?: string;
    idempotencyKey: string;
  }): Promise<TimeOffRequest> {
    const body: Record<string, unknown> = {
      starts_at: input.startsAt,
      ends_at: input.endsAt,
      kind: input.kind ?? "vacation",
    };
    if (input.reason) body.reason = input.reason;
    return this.client.request<TimeOffRequest>("/api/wfm/time-off", {
      method: "POST",
      body,
      idempotencyKey: input.idempotencyKey,
    });
  }

  async cancelTimeOff(requestId: string): Promise<TimeOffRequest> {
    return this.client.request<TimeOffRequest>(`/api/wfm/time-off/${requestId}/cancel`, {
      method: "POST",
      body: {},
    });
  }

  async mySwaps(): Promise<SwapOffer[]> {
    const response = await this.client.request<{ swaps: SwapOffer[] }>("/api/wfm/swaps");
    return response.swaps;
  }

  /** Offer one of your own shifts. A target person is required: an open offer to
   * "anyone" is a different feature (open-shift bidding) with a different approval. */
  async offerSwap(input: {
    shiftId: string;
    targetUserId: string;
    note?: string;
    idempotencyKey: string;
  }): Promise<SwapOffer> {
    const body: Record<string, unknown> = {
      shift_id: input.shiftId,
      target_user_id: input.targetUserId,
    };
    if (input.note) body.note = input.note;
    return this.client.request<SwapOffer>("/api/wfm/swaps", {
      method: "POST",
      body,
      idempotencyKey: input.idempotencyKey,
    });
  }

  async acceptSwap(swapId: string): Promise<SwapOffer> {
    return this.client.request<SwapOffer>(`/api/wfm/swaps/${swapId}/accept`, {
      method: "POST",
      body: {},
    });
  }
}
