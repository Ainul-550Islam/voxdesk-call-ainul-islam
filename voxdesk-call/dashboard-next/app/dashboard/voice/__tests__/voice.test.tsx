import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import VoiceAgentPage from "../page";
import { api, ApiError } from "@/lib/api";

// The page reads and writes real tenant voice profiles. The api module is the
// only thing stubbed: the assertions below are about what the page does with
// the responses it gets, not about the transport.

const PROFILE_A = {
  id: "profile-a",
  name: "Support — Rachael",
  provider: "elevenlabs",
  provider_voice_id: "rachel",
  language: "en-US",
  locale: "en-US",
  voice_type: "provider",
  status: "active",
  capabilities: {},
  metadata: {},
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const PROFILE_B = {
  ...PROFILE_A,
  id: "profile-b",
  name: "Sales — Adam",
  provider_voice_id: "adam",
  status: "inactive",
};

function profileList(items: typeof PROFILE_A[]) {
  return { items, total: items.length };
}

async function mountPage() {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(<VoiceAgentPage />);
  });
  await flush();
  return {
    host,
    async unmount() {
      await act(async () => root.unmount());
      host.remove();
    },
  };
}

/** Let the page's mount-time fetch and its state updates settle. */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function findButton(host: HTMLElement, testId: string): HTMLButtonElement {
  const el = host.querySelector<HTMLButtonElement>(`[data-testid="${testId}"]`);
  if (!el) throw new Error(`button ${testId} not found`);
  return el;
}

/**
 * Set a controlled input's value the way a keystroke does.
 *
 * React installs a value tracker on the element, so assigning `node.value`
 * directly is swallowed and `onChange` never fires.
 */
async function typeInto(node: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  await act(async () => {
    if (setter) setter.call(node, value);
    else node.value = value;
    node.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await flush();
}

function findInput(host: HTMLElement, testId: string): HTMLInputElement {
  const el = host.querySelector<HTMLInputElement>(`[data-testid="${testId}"]`);
  if (!el) throw new Error(`input ${testId} not found`);
  return el;
}

/** The voice library lives on the "Voice" step of the workspace stepper. */
async function openVoiceStep(host: HTMLElement) {
  const step = host.querySelector<HTMLButtonElement>(
    '[data-testid="voice-step-voice"]',
  );
  if (!step) throw new Error("voice step button not found");
  await act(async () => {
    step.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await flush();
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await flush();
}

let voiceProfiles: ReturnType<typeof vi.fn>;
let createVoiceProfile: ReturnType<typeof vi.fn>;
let activateVoiceProfile: ReturnType<typeof vi.fn>;
let deactivateVoiceProfile: ReturnType<typeof vi.fn>;

beforeEach(() => {
  voiceProfiles = vi.fn();
  createVoiceProfile = vi.fn();
  activateVoiceProfile = vi.fn();
  deactivateVoiceProfile = vi.fn();
  voiceProfiles.mockResolvedValue(profileList([]));
  vi.spyOn(api, "voiceProfiles").mockImplementation(voiceProfiles);
  vi.spyOn(api, "createVoiceProfile").mockImplementation(createVoiceProfile);
  vi.spyOn(api, "activateVoiceProfile").mockImplementation(activateVoiceProfile);
  vi.spyOn(api, "deactivateVoiceProfile").mockImplementation(deactivateVoiceProfile);
  vi.spyOn(api, "voiceCloneJobs").mockResolvedValue([]);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Voice page — real profile list, create and activation", () => {
  it("renders the profiles the server returned", async () => {
    voiceProfiles.mockResolvedValue(profileList([PROFILE_A, PROFILE_B]));
    const view = await mountPage();
    try {
      await openVoiceStep(view.host);
      expect(voiceProfiles).toHaveBeenCalledTimes(1);
      expect(view.host.querySelector('[data-testid="voice-profile-row-profile-a"]')).toBeTruthy();
      expect(view.host.querySelector('[data-testid="voice-profile-row-profile-b"]')).toBeTruthy();
      const statusA = view.host.querySelector('[data-testid="voice-profile-status-profile-a"]');
      expect(statusA?.textContent).toBe("active");
    } finally {
      await view.unmount();
    }
  });

  it("shows an empty state and no instructions to call an API by hand", async () => {
    voiceProfiles.mockResolvedValue(profileList([]));
    const view = await mountPage();
    try {
      await openVoiceStep(view.host);
      expect(view.host.querySelector('[data-testid="voice-profile-empty"]')).toBeTruthy();
      expect(view.host.textContent).not.toContain("POST /api/agent/voice-profiles");
    } finally {
      await view.unmount();
    }
  });

  it("surfaces a load failure instead of showing an empty library", async () => {
    voiceProfiles.mockRejectedValue(new ApiError(503, "voice service unavailable"));
    const view = await mountPage();
    try {
      await openVoiceStep(view.host);
      expect(view.host.querySelector('[data-testid="voice-page-loading"]')).toBeFalsy();
      expect(view.host.textContent).toContain("voice service unavailable");
    } finally {
      await view.unmount();
    }
  });

  it("replaces the row with the server's response when activating", async () => {
    voiceProfiles.mockResolvedValue(profileList([PROFILE_B]));
    activateVoiceProfile.mockResolvedValue({ ...PROFILE_B, status: "active" });
    const view = await mountPage();
    try {
      await openVoiceStep(view.host);
      await click(findButton(view.host, "voice-profile-toggle-profile-b"));
      expect(activateVoiceProfile).toHaveBeenCalledWith("profile-b");
      const status = view.host.querySelector('[data-testid="voice-profile-status-profile-b"]');
      // The server's word decides the displayed state, not an optimistic flip.
      expect(status?.textContent).toBe("active");
    } finally {
      await view.unmount();
    }
  });

  it("deactivates a profile that is currently active", async () => {
    voiceProfiles.mockResolvedValue(profileList([PROFILE_A]));
    deactivateVoiceProfile.mockResolvedValue({ ...PROFILE_A, status: "inactive" });
    const view = await mountPage();
    try {
      await openVoiceStep(view.host);
      await click(findButton(view.host, "voice-profile-toggle-profile-a"));
      expect(deactivateVoiceProfile).toHaveBeenCalledWith("profile-a");
      expect(activateVoiceProfile).not.toHaveBeenCalled();
      const status = view.host.querySelector('[data-testid="voice-profile-status-profile-a"]');
      expect(status?.textContent).toBe("inactive");
    } finally {
      await view.unmount();
    }
  });

  it("keeps the stored status when activation is rejected", async () => {
    voiceProfiles.mockResolvedValue(profileList([PROFILE_B]));
    activateVoiceProfile.mockRejectedValue(new ApiError(502, "provider rejected the voice"));
    const view = await mountPage();
    try {
      await openVoiceStep(view.host);
      await click(findButton(view.host, "voice-profile-toggle-profile-b"));
      expect(view.host.textContent).toContain("provider rejected the voice");
      const status = view.host.querySelector('[data-testid="voice-profile-status-profile-b"]');
      expect(status?.textContent).toBe("inactive");
    } finally {
      await view.unmount();
    }
  });

  it("posts the typed values and appends the created profile", async () => {
    voiceProfiles.mockResolvedValue(profileList([]));
    createVoiceProfile.mockResolvedValue(PROFILE_A);
    const view = await mountPage();
    try {
      await openVoiceStep(view.host);
      const name = findInput(view.host, "voice-profile-name-input");
      const voiceId = findInput(view.host, "voice-profile-voice-id-input");
      await typeInto(name, "Support — Rachael");
      await typeInto(voiceId, "rachel");
      await click(findButton(view.host, "voice-profile-create-btn"));
      expect(createVoiceProfile).toHaveBeenCalledTimes(1);
      expect(createVoiceProfile.mock.calls[0][0]).toMatchObject({
        name: "Support — Rachael",
        provider_voice_id: "rachel",
        provider: "elevenlabs",
      });
      expect(view.host.querySelector('[data-testid="voice-profile-row-profile-a"]')).toBeTruthy();
    } finally {
      await view.unmount();
    }
  });

  it("refuses to submit an incomplete profile locally", async () => {
    voiceProfiles.mockResolvedValue(profileList([]));
    const view = await mountPage();
    try {
      await openVoiceStep(view.host);
      await click(findButton(view.host, "voice-profile-create-btn"));
      expect(createVoiceProfile).not.toHaveBeenCalled();
      expect(view.host.textContent).toContain("Profile name and provider voice id are required.");
    } finally {
      await view.unmount();
    }
  });
});
