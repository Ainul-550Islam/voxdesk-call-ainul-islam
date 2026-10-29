import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TranslationPage from "../page";
import { api, ApiError } from "@/lib/api";

// The page lists jobs from GET /api/translation/jobs and renders a job's
// segments from its json export. Only the api module is stubbed; the
// assertions are about what the page does with the payloads it receives.

const JOB_ONE = {
  execution_id: "exec-1",
  id: "exec-1",
  status: "completed",
  execution_status: "completed",
  source_language: "en",
  target_language: "es",
  glossary_version: "1.0",
  segment_count: 2,
  total_segments: 2,
  completed_segments: 2,
  failed_segments: 0,
  quality_status: "passed",
  review_required: false,
  review_state: "NOT_REQUIRED",
  attempt_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  completed_at: "2026-01-02T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

const JOB_TWO = {
  ...JOB_ONE,
  execution_id: "exec-2",
  id: "exec-2",
  status: "failed",
  completed_segments: 1,
  failed_segments: 1,
  quality_status: "review",
  review_required: true,
  review_state: "REQUIRED",
};

const DOCUMENT = {
  execution_id: "exec-1",
  id: "exec-1",
  format: "json",
  status: "completed",
  source_language: "en",
  target_language: "es",
  glossary_version: "1.0",
  quality_status: "passed",
  review_required: false,
  review_state: "NOT_REQUIRED",
  provider: "openai",
  model: "gpt-4o",
  methodology: "llm",
  segment_count: 2,
  segments: [
    {
      segment_id: "s1",
      source_text: "Hello, thank you for calling.",
      translated_text: "Hola, gracias por llamar.",
      source_fingerprint: "aaa111",
      target_fingerprint: "bbb222",
      quality_flags: [],
      review_required: false,
      status: "completed",
    },
    {
      segment_id: "s2",
      source_text: "Your appointment is confirmed.",
      translated_text: "Su cita esta confirmada.",
      source_fingerprint: "ccc333",
      target_fingerprint: "ddd444",
      quality_flags: ["placeholder_mismatch"],
      review_required: true,
      status: "completed",
    },
  ],
  exported_at: "2026-01-02T00:00:00Z",
};

const SRT_BODY = "1\n00:00:00,000 --> 00:00:02,000\nHola, gracias por llamar.\n";

async function mountPage() {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(<TranslationPage />);
  });
  return {
    host,
    async unmount() {
      await act(async () => root.unmount());
      host.remove();
    },
  };
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function el<T extends HTMLElement>(host: HTMLElement, testId: string): T {
  const found = host.querySelector<T>(`[data-testid="${testId}"]`);
  if (!found) throw new Error(`${testId} not found`);
  return found;
}

async function click(node: HTMLElement) {
  await act(async () => {
    node.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await flush();
}

async function typeInto(node: HTMLInputElement, value: string) {
  // React installs a value tracker on the element, so assigning `node.value`
  // directly is swallowed. The prototype setter is what a real keystroke uses.
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

async function enterScope(view: { host: HTMLElement }) {
  await typeInto(el<HTMLInputElement>(view.host, "translation-tenant-input"), "tenant-1");
  await typeInto(
    el<HTMLInputElement>(view.host, "translation-environment-input"),
    "env-1",
  );
  await click(el(view.host, "translation-scope-apply"));
  await flush();
}

let translationJobs: ReturnType<typeof vi.fn>;
let translationExportDocument: ReturnType<typeof vi.fn>;
let exportTranslationJob: ReturnType<typeof vi.fn>;

beforeEach(() => {
  translationJobs = vi.fn().mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  translationExportDocument = vi.fn().mockResolvedValue(null);
  exportTranslationJob = vi.fn().mockResolvedValue("");
  vi.spyOn(api, "translationJobs").mockImplementation(translationJobs);
  vi.spyOn(api, "translationExportDocument").mockImplementation(translationExportDocument);
  vi.spyOn(api, "exportTranslationJob").mockImplementation(exportTranslationJob);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Translation page — real job list and exports", () => {
  it("asks for the environment before listing anything", async () => {
    const view = await mountPage();
    try {
      await flush();
      expect(translationJobs).not.toHaveBeenCalled();
      expect(el(view.host, "translation-scope-form")).toBeTruthy();
    } finally {
      await view.unmount();
    }
  });

  it("lists the jobs the server returned for the environment", async () => {
    translationJobs.mockResolvedValue({
      items: [JOB_ONE, JOB_TWO],
      total: 2,
      limit: 50,
      offset: 0,
    });
    const view = await mountPage();
    try {
      await enterScope(view);
      expect(translationJobs).toHaveBeenCalledWith({
        environment_id: "env-1",
        limit: 50,
      });
      expect(el(view.host, "translation-job-exec-1")).toBeTruthy();
      expect(el(view.host, "translation-job-exec-2")).toBeTruthy();
      expect(el(view.host, "translation-job-status-exec-2").textContent).toBe("failed");
      expect(el(view.host, "translation-scope-readout").textContent).toContain("2 jobs");
    } finally {
      await view.unmount();
    }
  });

  it("renders an empty state rather than sample rows", async () => {
    const view = await mountPage();
    try {
      await enterScope(view);
      expect(el(view.host, "translation-empty").textContent).toContain(
        "No translation jobs recorded in this environment yet.",
      );
      // No fabricated demo segment may reach the screen.
      expect(view.host.textContent).not.toContain("limitation of liability");
    } finally {
      await view.unmount();
    }
  });

  it("reports a failed job list instead of an empty one", async () => {
    translationJobs.mockRejectedValue(new ApiError(403, "forbidden"));
    const view = await mountPage();
    try {
      await enterScope(view);
      expect(el(view.host, "translation-error").textContent).toContain("forbidden");
      expect(view.host.querySelector('[data-testid="translation-empty"]')).toBeFalsy();
    } finally {
      await view.unmount();
    }
  });

  it("passes a status filter through to the request", async () => {
    translationJobs.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    const view = await mountPage();
    try {
      await enterScope(view);
      await typeInto(el<HTMLInputElement>(view.host, "translation-status-filter"), "failed");
      await click(el(view.host, "translation-refresh"));
      expect(translationJobs).toHaveBeenLastCalledWith({
        environment_id: "env-1",
        status: "failed",
        limit: 50,
      });
    } finally {
      await view.unmount();
    }
  });

  it("renders a selected job's segments from its json export", async () => {
    translationJobs.mockResolvedValue({
      items: [JOB_ONE],
      total: 1,
      limit: 50,
      offset: 0,
    });
    translationExportDocument.mockResolvedValue(DOCUMENT);
    const view = await mountPage();
    try {
      await enterScope(view);
      await click(el(view.host, "translation-job-exec-1"));
      expect(translationExportDocument).toHaveBeenCalledWith("exec-1", "env-1");
      expect(el(view.host, "translation-segment-s1").textContent).toContain(
        "Hola, gracias por llamar.",
      );
      expect(el(view.host, "translation-segment-s2").textContent).toContain(
        "placeholder_mismatch",
      );
      expect(view.host.textContent).toContain("gpt-4o");
    } finally {
      await view.unmount();
    }
  });

  it("exports the selected job in each supported format", async () => {
    translationJobs.mockResolvedValue({
      items: [JOB_ONE],
      total: 1,
      limit: 50,
      offset: 0,
    });
    translationExportDocument.mockResolvedValue(DOCUMENT);
    exportTranslationJob.mockResolvedValue(SRT_BODY);
    const view = await mountPage();
    try {
      await enterScope(view);
      await click(el(view.host, "translation-job-exec-1"));

      await click(el(view.host, "translation-export-srt"));
      expect(exportTranslationJob).toHaveBeenLastCalledWith("exec-1", "env-1", "srt");
      expect(el(view.host, "translation-export-body").textContent).toContain(
        "00:00:00,000 --> 00:00:02,000",
      );

      await click(el(view.host, "translation-export-txt"));
      expect(exportTranslationJob).toHaveBeenLastCalledWith("exec-1", "env-1", "txt");

      await click(el(view.host, "translation-export-json"));
      expect(exportTranslationJob).toHaveBeenLastCalledWith("exec-1", "env-1", "json");
      expect(el(view.host, "translation-export-format").textContent).toContain("json");
    } finally {
      await view.unmount();
    }
  });

  it("shows the export error when the server refuses an export", async () => {
    translationJobs.mockResolvedValue({
      items: [JOB_TWO],
      total: 1,
      limit: 50,
      offset: 0,
    });
    translationExportDocument.mockResolvedValue(DOCUMENT);
    exportTranslationJob.mockRejectedValue(new ApiError(409, "job is not exportable yet"));
    const view = await mountPage();
    try {
      await enterScope(view);
      await click(el(view.host, "translation-job-exec-2"));
      await click(el(view.host, "translation-export-srt"));
      expect(el(view.host, "translation-error").textContent).toContain(
        "job is not exportable yet",
      );
      expect(view.host.querySelector('[data-testid="translation-export-body"]')).toBeFalsy();
    } finally {
      await view.unmount();
    }
  });
});
