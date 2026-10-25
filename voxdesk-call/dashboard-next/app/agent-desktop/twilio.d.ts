import type { IceServer } from "./types";

export interface TwilioConnection {
  on(event: "error" | "disconnect", listener: (value?: unknown) => void): unknown;
  sendDigits(digits: string): void;
  disconnect(): void;
}

export interface TwilioDevice {
  on(event: "registered" | "error", listener: (value?: unknown) => void): unknown;
  register(): Promise<void>;
  updateToken(token: string): Promise<void> | void;
  destroy(): void;
  connect(options: {
    params: Record<string, string>;
    rtcConfiguration: { iceServers: IceServer[] };
  }): Promise<TwilioConnection>;
}

declare global {
  interface Window {
    Twilio?: {
      Device: new (token: string) => TwilioDevice;
    };
  }
}

export {};
