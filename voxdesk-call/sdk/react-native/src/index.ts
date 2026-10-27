/**
 * `@voxdesk/mobile` — the React Native client for the VoxDesk mobile API.
 *
 * ```ts
 * const client = new VoxDeskClient({
 *   baseUrl: "https://acme.voxdesk.example",
 *   getAccessToken: () => keychain.get("access_token"),
 * });
 * const devices = new DeviceApi(client);
 * await devices.register({ platform: "ios", deviceId: await DeviceInfo.getUniqueId() });
 * ```
 */

export { VoxDeskClient } from "./client.js";
export { ApiError, NetworkError, parseErrorBody } from "./errors.js";
export { DeviceApi, PLATFORMS, validateQuietHours } from "./devices.js";
export { PushApi, inQuietHours, localMinutes } from "./push.js";
export { WfmApi } from "./wfm.js";
export type {
  ApiErrorDetail,
  ClientOptions,
  Delivery,
  DeliveryAttempt,
  DeliveryList,
  Device,
  FetchLike,
  Platform,
  ProviderStatus,
  PushTokenSummary,
  QuietHours,
  RegisterDeviceInput,
  RequestOptions,
  ResponseLike,
} from "./types.js";
export type { DeviceListResponse } from "./devices.js";
export type { PushTestInput, PushTestResult } from "./push.js";
export type {
  Shift,
  ShiftsResponse,
  SwapOffer,
  TimeOffBalance,
  TimeOffKind,
  TimeOffRequest,
  TimeOffStatus,
} from "./wfm.js";
