import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buttonByText, render, typeInto } from "./harness";

/**
 * Page-level tests for the platform console.
 *
 * Each test asserts on what an operator reads, and on the exact payload the page sends.
 * The two failure modes worth guarding against are both about honesty rather than
 * styling: a panel that reports zero when the request failed, and a screen that says a
 * notification was delivered when the platform only knows it was dropped.
 */

const api = {
  historyCounts: vi.fn(),
  readAccuracy: vi.fn(),
  listDomains: vi.fn(),
  providerStatus: vi.fn(),
  listForecasts: vi.fn(),
  listTemplates: vi.fn(),
  listLaborRules: vi.fn(),
  readStaffing: vi.fn(),
  createForecast: vi.fn(),
  listSchedules: vi.fn(),
  readSchedule: vi.fn(),
  solveSchedule: vi.fn(),
  publishSchedule: vi.fn(),
  listTimeOff: vi.fn(),
  decideTimeOff: vi.fn(),
  createDomain: vi.fn(),
  verifyDomain: vi.fn(),
  domainChecks: vi.fn(),
  readCertificate: vi.fn(),
  readTheme: vi.fn(),
  validateTheme: vi.fn(),
  publishTheme: vi.fn(),
  listAssets: vi.fn(),
  listSenderDomains: vi.fn(),
  senderRecords: vi.fn(),
  verifySender: vi.fn(),
  listDevices: vi.fn(),
  listDeliveries: vi.fn(),
  sendTestPush: vi.fn(),
};

vi.mock("../lib/api", () => ({ plat2Api: api, idempotencyKey: (prefix: string) => `${prefix}:test` }));

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  // Sensible empty defaults; each test overrides only what it is about.
  api.historyCounts.mockResolvedValue({
    intervals: 0,
    days: 0,
    first: null,
    last: null,
    required_days: 21,
    enough: false,
    workday_minutes: 1440,
  });
  api.readAccuracy.mockResolvedValue({
    queue_key: "",
    count: 0,
    trend: { samples: 0, mean_wape: 0, last_wape: 0, direction: "unknown" },
    evaluations: [],
  });
  api.listDomains.mockResolvedValue({ domains: [], count: 0 });
  api.providerStatus.mockResolvedValue({
    fcm: { enabled: false, configured: false },
    apns: { enabled: false, configured: false, sandbox: true },
    quiet_hours_max_span_hours: 12,
    token_gc_days: 30,
  });
  api.listForecasts.mockResolvedValue({ forecasts: [] });
  api.listTemplates.mockResolvedValue({ templates: [] });
  api.listLaborRules.mockResolvedValue({ rules: [] });
  api.listSchedules.mockResolvedValue({ schedules: [] });
  api.listTimeOff.mockResolvedValue({ requests: [] });
  api.domainChecks.mockResolvedValue({ checks: [] });
  api.readCertificate.mockResolvedValue(null);
  api.listAssets.mockResolvedValue({ assets: [] });
  api.listSenderDomains.mockResolvedValue({ sender_domains: [] });
  api.senderRecords.mockResolvedValue({ records: {}, checks: [] });
  api.listDevices.mockResolvedValue({ devices: [], count: 0 });
  api.listDeliveries.mockResolvedValue({ deliveries: [], summary: {} });
  api.readSchedule.mockResolvedValue(null);
  api.readStaffing.mockResolvedValue(null);
  api.readTheme.mockResolvedValue({ payload: {}, version: null, defaults: {}, allowed_fonts: [] });
  api.readCertificate.mockResolvedValue(null);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("overview", () => {
  it("shows history counts and never invents an accuracy figure", async () => {
    const { default: Overview } = await import("../page");
    api.historyCounts.mockResolvedValue({
      intervals: 2880,
      days: 30,
      first: "2026-09-01",
      last: "2026-10-01",
      required_days: 21,
      enough: true,
      workday_minutes: 1440,
    });
    // A trend with no scored forecast still arrives from the API; the panel must
    // decline to turn it into an accuracy figure.
    api.readAccuracy.mockResolvedValue({
      queue_key: "",
      count: 0,
      trend: { samples: 0, mean_wape: 0, last_wape: 0, direction: "unknown" },
      evaluations: [],
    });
    const view = await render(<Overview />);

    expect(view.text()).toContain("2,880");
    expect(view.text()).toContain("30 days of history");
    expect(view.text()).toContain("No scored forecast yet");
    // The accuracy metric shows a dash, not 0.0% — "not measured" is not "perfect".
    expect(view.text()).not.toContain("0.0%");
    await view.unmount();
  });

  it("renders an accuracy fraction as a percentage", async () => {
    const { default: Overview } = await import("../page");
    api.readAccuracy.mockResolvedValue({
      queue_key: "",
      count: 1,
      trend: { samples: 4, mean_wape: 0.204, last_wape: 0.128, direction: "improving" },
      evaluations: [
        {
          forecast_id: "f1",
          evaluated_from: "2026-10-01T00:00:00Z",
          evaluated_to: "2026-10-05T00:00:00Z",
          wape: 0.128,
          mape: 0.204,
          bias: -0.031,
          mae: 3.2,
          sample_count: 96,
        },
      ],
    });
    const view = await render(<Overview />);

    expect(view.text()).toContain("12.8%");
    expect(view.text()).toContain("20.4%");
    expect(view.text()).toContain("improving");
    await view.unmount();
  });

  it("reports a failed request as a failure instead of zero", async () => {
    const { default: Overview } = await import("../page");
    api.historyCounts.mockRejectedValue(new Error("403 forbidden"));
    const view = await render(<Overview />);

    expect(view.text()).toContain("History unavailable");
    expect(view.text()).toContain("403 forbidden");
    expect(view.findAll('[role="alert"]').length).toBeGreaterThan(0);
    await view.unmount();
  });
});

describe("workforce", () => {
  it("fits a forecast from history with the queue key and horizon the operator typed", async () => {
    const { default: Workforce } = await import("../workforce/page");
    api.createForecast.mockResolvedValue({
      id: "fc-1",
      queue_key: "sales",
      timezone: "Asia/Dhaka",
      interval_minutes: 15,
      generated_at: "2026-10-26T00:00:00Z",
      horizon_days: 7,
      history_days: 28,
      method: "multiplicative",
      model_version: "v1",
      points: 672,
      total_expected: 1200,
      peak: 12,
      overrides: {},
      daily_totals: { "2026-10-27": 400, "2026-10-28": 800 },
    });
    api.readStaffing.mockResolvedValue({
      queue_key: "sales",
      timezone: "UTC",
      band: "expected",
      // The target block is where the assumptions live: the plan reports the
      // parameters it was actually built with, not the ones the form happens to show.
      target: {
        service_level: 0.8,
        answer_seconds: 20,
        max_occupancy: 0.85,
        shrinkage: 0.3,
        average_handle_seconds: 180,
        average_patience_seconds: 300,
        minimum_agents: 0,
        paid_multiplier: 1.2,
        model: "erlang_c",
      },
      intervals: 672,
      peak_paid_agents: 6,
      peak_at: "2026-10-27T09:00:00Z",
      productive_hours: 62.5,
      paid_hours: 74.5,
      daily: { "2026-10-27": { paid_agents_peak: 5, paid_hours: 40, volume: 400 } },
      curve: [],
      distribution: { min: 2, median: 4, mean: 4.1, max: 6, p90: 6 },
    });

    const view = await render(<Workforce />);
    await typeInto(view, "Queue key", "sales");
    await typeInto(view, "Horizon", "7");
    await view.click(buttonByText(view, "Fit from history"));

    expect(api.createForecast).toHaveBeenCalledWith({ queue_key: "sales", horizon_days: 7 });
    // Selecting the new forecast pulls a staffing plan with the same parameters on screen.
    const staffingArgs = api.readStaffing.mock.calls[0];
    expect(staffingArgs[0]).toBe("fc-1");
    expect(staffingArgs[1]).toMatchObject({
      serviceLevel: 0.8,
      answerSeconds: 20,
      maxOccupancy: 0.85,
      shrinkage: 0.3,
    });
    await view.unmount();
  });

  it("marks a template whose paid time exceeds the shift cap", async () => {
    const { default: Workforce } = await import("../workforce/page");
    api.listTemplates.mockResolvedValue({
      templates: [
        {
          id: "t1",
          name: "Long day",
          start_minute: 480,
          length_minutes: 660,
          paid_break_minutes: 0,
          unpaid_break_minutes: 30,
          required_skill_key: "voice",
          is_active: true,
        },
      ],
    });
    api.listLaborRules.mockResolvedValue({
      rules: [
        {
          id: "r1",
          name: "Default",
          max_hours_per_week: 48,
          min_rest_hours: 10,
          max_consecutive_days: 6,
          max_shift_hours: 10,
          min_split_gap_minutes: 60,
          is_active: true,
        },
      ],
    });
    const view = await render(<Workforce />);

    // 660 − 30 = 630 paid minutes = 10h 30m, over the 10h cap.
    expect(view.text()).toContain("10h 30m");
    expect(view.text()).toContain("Over shift cap");
    await view.unmount();
  });
});

describe("schedules", () => {
  it("publishes the version it is looking at, not whatever is current", async () => {
    const { default: SchedulePage } = await import("../schedule/page");
    api.listSchedules.mockResolvedValue({
      schedules: [
        {
          id: "s1",
          name: "Week 40",
          week_start: "2026-10-05",
          timezone: "Asia/Dhaka",
          status: "draft",
          current_version_id: null,
        },
      ],
    });
    api.readSchedule.mockResolvedValue({
      id: "s1",
      name: "Week 40",
      week_start: "2026-10-05",
      timezone: "Asia/Dhaka",
      status: "draft",
      current_version_id: null,
      versions: [
        {
          version_id: "v2",
          version_number: 2,
          status: "draft",
          assignments: 14,
          coverage: {},
          solver: { status: "OPTIMAL", coverage_ratio: 0.93, objective: 12.5 },
          created_at: "2026-10-26T00:00:00Z",
          published_at: null,
        },
      ],
      shifts: [],
    });
    api.publishSchedule.mockResolvedValue({
      schedule_id: "s1",
      version_id: "v2",
      version_number: 2,
      published_at: "2026-10-26T01:00:00Z",
      diff: {},
      notifications: [{ user_id: "u1", changes: 1, items: [] }],
    });

    const view = await render(<SchedulePage />);
    await view.click(buttonByText(view, "Open"));
    expect(view.text()).toContain("93.0%");

    await view.click(buttonByText(view, "Publish"));
    expect(api.publishSchedule).toHaveBeenCalledWith("s1", { version_id: "v2" });
    expect(view.text()).toContain("Published version 2");
    await view.unmount();
  });

  it("reports shortfall instead of rounding coverage up", async () => {
    const { default: SchedulePage } = await import("../schedule/page");
    api.listSchedules.mockResolvedValue({
      schedules: [
        {
          id: "s1",
          name: "Week 40",
          week_start: "2026-10-05",
          timezone: "Asia/Dhaka",
          status: "draft",
          current_version_id: null,
        },
      ],
    });
    api.solveSchedule.mockResolvedValue({
      status: "FEASIBLE",
      assignments: 9,
      hours_by_user: { u1: 37.5 },
      shortfall_agent_intervals: 4,
      overcoverage_agent_intervals: 0,
      objective: 80,
      wall_time_seconds: 0.4,
      intervals: 672,
      coverage_ratio: 0.62,
      rules: {},
    });

    const view = await render(<SchedulePage />);
    await view.click(buttonByText(view, "Open"));
    await view.click(buttonByText(view, "Solve week"));

    expect(view.text()).toContain("62.0%");
    expect(view.text()).toContain("Short-staffed");
    expect(view.text()).toContain("4");
    await view.unmount();
  });

  it("sends an explicit approval decision for time off", async () => {
    const { default: SchedulePage } = await import("../schedule/page");
    api.listTimeOff.mockResolvedValue({
      requests: [
        {
          id: "req-1",
          user_id: "u1",
          kind: "vacation",
          starts_at: "2026-10-12T00:00:00Z",
          ends_at: "2026-10-13T00:00:00Z",
          hours: 8,
          status: "requested",
          reason: "",
          coverage: {},
          decided_at: null,
        },
      ],
    });
    api.decideTimeOff.mockResolvedValue({} as never);

    const view = await render(<SchedulePage />);
    await view.click(buttonByText(view, "Approve"));

    expect(api.decideTimeOff).toHaveBeenCalledWith("req-1", { approved: true });
    await view.unmount();
  });
});

describe("domains", () => {
  it("shows the ownership token once, at claim time", async () => {
    const { default: DomainsPage } = await import("../domains/page");
    api.createDomain.mockResolvedValue({
      id: "d1",
      domain: "support.example.com",
      status: "pending_dns",
      environment_id: null,
      ownership_record_name: "_voxdesk.support.example.com",
      cname_target: "edge.voxdesk.app",
      last_checked_at: null,
      verified_at: null,
      activated_at: null,
      reverify_due_at: null,
      failure_reason: "",
      created_at: "2026-10-26T00:00:00Z",
      instructions: {
        ownership: { record_type: "TXT", name: "_voxdesk.support.example.com", value: "token" },
        routing: { record_type: "CNAME", name: "support.example.com", value: "edge.voxdesk.app" },
      },
      ownership_token: "shown-once-token",
      ownership_token_notice: "shown once",
    });

    const view = await render(<DomainsPage />);
    await typeInto(view, "Hostname", "support.example.com");
    await view.click(buttonByText(view, "Claim"));

    expect(api.createDomain).toHaveBeenCalledWith({ domain: "support.example.com" });
    expect(view.text()).toContain("shown-once-token");
    expect(view.text()).toContain("shown once");
    await view.unmount();
  });

  it("does not paint a pending domain as live", async () => {
    const { default: DomainsPage } = await import("../domains/page");
    api.listDomains.mockResolvedValue({
      domains: [
        {
          id: "d1",
          domain: "support.example.com",
          status: "pending_dns",
          environment_id: null,
          ownership_record_name: "_voxdesk.support.example.com",
          cname_target: "edge.voxdesk.app",
          last_checked_at: null,
          verified_at: null,
          activated_at: null,
          reverify_due_at: null,
          failure_reason: "TXT record missing",
          created_at: null,
          instructions: {
            ownership: { record_type: "TXT", name: "_voxdesk", value: "token" },
            routing: { record_type: "CNAME", name: "support.example.com", value: "edge.voxdesk.app" },
          },
        },
      ],
      count: 1,
    });

    const view = await render(<DomainsPage />);
    expect(view.text()).toContain("Waiting for DNS");
    expect(view.text()).toContain("TXT record missing");
    // No status pill may read "Live": only an active domain is routable.
    const live = view
      .findAll("[data-tone]")
      .filter((element) => (element.textContent ?? "").trim() === "Live");
    expect(live).toHaveLength(0);
    await view.unmount();
  });
});

describe("email sender domains", () => {
  const sender = {
    id: "s1",
    domain: "mail.example.com",
    from_name: "Example Support",
    dkim_selector: "voxdesk",
    status: "pending",
    spf_verified: false,
    dkim_verified: false,
    dmarc_verified: false,
    records: {
      spf: { record_type: "TXT", name: "mail.example.com", value: "v=spf1 include:...", purpose: "SPF" },
      dkim: { record_type: "TXT", name: "voxdesk._domainkey.mail.example.com", value: "v=DKIM1; p=...", purpose: "DKIM" },
      dmarc: { record_type: "TXT", name: "_dmarc.mail.example.com", value: "v=DMARC1; p=quarantine", purpose: "DMARC" },
    },
    last_checked_at: null,
    verified_at: null,
    created_at: null,
  };

  it("shows what to publish before anything has been checked", async () => {
    const { default: EmailPage } = await import("../email/page");
    api.listSenderDomains.mockResolvedValue({ sender_domains: [sender] });
    api.senderRecords.mockResolvedValue({ records: sender.records, checks: [] });

    const view = await render(<EmailPage />);
    await view.click(buttonByText(view, "Records"));

    // The DNS rows come from the create payload, so an operator can act on day one.
    expect(view.text()).toContain("voxdesk._domainkey.mail.example.com");
    expect(view.text()).toContain("v=DMARC1; p=quarantine");
    expect(view.text()).toContain("No record has been checked yet");
    await view.unmount();
  });

  it("reports a failed check as failed, not as pending", async () => {
    const { default: EmailPage } = await import("../email/page");
    api.listSenderDomains.mockResolvedValue({ sender_domains: [sender] });
    api.senderRecords.mockResolvedValue({
      records: sender.records,
      checks: [
        {
          id: "c1",
          record_type: "TXT",
          name: "mail.example.com",
          expected: "v=spf1 include:...",
          observed: ["v=spf1 -all"],
          passed: false,
          detail: { present: true, valid: false, reason: "include does not match" },
          checked_at: "2026-10-26T00:00:00Z",
        },
      ],
    });

    const view = await render(<EmailPage />);
    await view.click(buttonByText(view, "Records"));

    expect(view.text()).toContain("v=spf1 -all");
    expect(view.text()).toContain("Fail");
    await view.unmount();
  });
});

describe("branding", () => {
  it("blocks publishing while contrast fails", async () => {
    const { default: BrandingPage } = await import("../branding/page");
    api.readTheme.mockResolvedValue({
      payload: { colors: { text: "#777777", background: "#ffffff" } },
      version: null,
      defaults: {},
      allowed_fonts: ["Inter"],
    });
    api.validateTheme.mockResolvedValue({
      valid: true,
      payload: {},
      contrast: {
        pairs: [
          {
            foreground: "text",
            background: "background",
            ratio: 4.478,
            minimum: 4.5,
            passed: false,
          },
        ],
        failures: ["text on background is 4.478:1, needs 4.5:1"],
        passed: false,
      },
    });

    const view = await render(<BrandingPage />);
    await view.click(buttonByText(view, "Validate contrast"));

    expect(view.text()).toContain("4.48:1");
    const publish = buttonByText(view, "Publish theme") as HTMLButtonElement;
    expect(publish.disabled).toBe(true);
    expect(api.publishTheme).not.toHaveBeenCalled();
    await view.unmount();
  });
});

describe("mobile push", () => {
  it("names the reason an attempt was dropped and never claims delivery", async () => {
    const { default: MobilePage } = await import("../mobile/page");
    api.sendTestPush.mockResolvedValue({
      attempts: [{ device_id: "dev-1", platform: "ios", status: "dropped", reason: "quiet_hours" }],
      note: "A dropped attempt names its reason.",
    });
    const view = await render(<MobilePage />);
    await view.click(buttonByText(view, "Send test push"));

    expect(view.text()).toContain("quiet_hours");
    expect(view.text()).toContain("dropped");
    expect(view.text().toLowerCase()).not.toContain("delivered to");
    await view.unmount();
  });

  it("shows quiet hours and token hints, not token values", async () => {
    const { default: MobilePage } = await import("../mobile/page");
    api.listDevices.mockResolvedValue({
      devices: [
        {
          id: "dev-1",
          user_id: "u1",
          platform: "ios",
          device_id: "9F1C",
          app_version: "1.4.0",
          os_version: "18.1",
          push_enabled: true,
          quiet_hours: { start_minute: 1320, end_minute: 420 },
          last_seen_at: "2026-10-26T04:00:00Z",
          revoked_at: null,
          created_at: "2026-09-01T00:00:00Z",
          tokens: [
            {
              id: "tok-1",
              platform: "ios",
              hint: "…a1b2c3",
              active: true,
              failure_count: 0,
              last_error: "",
              invalidated_at: null,
            },
          ],
        },
      ],
      count: 1,
    });
    const view = await render(<MobilePage />);

    expect(view.text()).toContain("22:00–07:00");
    expect(view.text()).toContain("1/1");
    await view.unmount();
  });
});
