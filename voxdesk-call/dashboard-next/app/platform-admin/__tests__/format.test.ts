import { describe, expect, it } from "vitest";

import {
  barWidthPercent,
  certificateTone,
  clockFromMinute,
  contrastFailures,
  coverageVerdict,
  dayLabel,
  deliverySummaryLabel,
  deliveryTone,
  domainStatusLabel,
  domainTone,
  durationLabel,
  formatNumber,
  formatPercent,
  formatSignedPercent,
  paidMinutes,
  quietHoursLabel,
  scheduleTone,
  shiftsByDay,
  skillKeys,
  summariseDaily,
  templateViolations,
  timeOffTone,
  timestampLabel,
  worstDomainStatus,
} from "../lib/format";

describe("percent formatting keeps fractions and percents apart", () => {
  it("renders a fraction as a percentage", () => {
    // Accuracy arrives as a fraction. Printing "0.128%" for a 12.8% error is the bug
    // this function exists to make impossible at the call site.
    expect(formatPercent(0.128)).toBe("12.8%");
    expect(formatPercent(0.128, 2)).toBe("12.80%");
    expect(formatPercent(1)).toBe("100.0%");
    expect(formatPercent(0)).toBe("0.0%");
  });

  it("renders a missing metric as an em dash rather than NaN", () => {
    expect(formatPercent(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
    expect(formatPercent(Number.NaN)).toBe("—");
    expect(formatPercent(Number.POSITIVE_INFINITY)).toBe("—");
  });

  it("signs a bias so direction is visible", () => {
    expect(formatSignedPercent(0.042)).toBe("+4.2%");
    expect(formatSignedPercent(-0.042)).toBe("-4.2%");
    expect(formatSignedPercent(0)).toBe("0.0%");
    expect(formatSignedPercent(null)).toBe("—");
  });

  it("formats plain numbers with a dash when absent", () => {
    expect(formatNumber(12.34)).toBe("12.3");
    expect(formatNumber(12.34, 2)).toBe("12.34");
    expect(formatNumber(null)).toBe("—");
  });
});

describe("minutes and durations", () => {
  it("turns minutes since midnight into a clock reading", () => {
    expect(clockFromMinute(0)).toBe("00:00");
    expect(clockFromMinute(540)).toBe("09:00");
    expect(clockFromMinute(570)).toBe("09:30");
    expect(clockFromMinute(1439)).toBe("23:59");
  });

  it("wraps past midnight instead of printing 25:00", () => {
    expect(clockFromMinute(1440)).toBe("00:00");
    expect(clockFromMinute(1500)).toBe("01:00");
    expect(clockFromMinute(-60)).toBe("23:00");
  });

  it("labels a missing minute as a dash", () => {
    expect(clockFromMinute(null)).toBe("—");
    expect(clockFromMinute(Number.NaN)).toBe("—");
  });

  it("renders durations without a spurious 0m", () => {
    expect(durationLabel(480)).toBe("8h");
    expect(durationLabel(450)).toBe("7h 30m");
    expect(durationLabel(0)).toBe("0m");
    expect(durationLabel(45)).toBe("45m");
    expect(durationLabel(null)).toBe("—");
  });
});

describe("timestamps and days", () => {
  it("labels an ISO day without letting the zone shift the date", () => {
    expect(dayLabel("2026-10-26")).toBe("Mon 26 Oct");
    expect(dayLabel("2026-03-29")).toBe("Sun 29 Mar");
  });

  it("passes through anything that is not an ISO date", () => {
    expect(dayLabel("not-a-date")).toBe("not-a-date");
    expect(dayLabel(null)).toBe("—");
  });

  it("renders a timestamp and refuses to invent one", () => {
    expect(timestampLabel(null)).toBe("—");
    expect(timestampLabel("nonsense")).toBe("—");
    expect(timestampLabel("2026-10-26T09:30:00Z")).toMatch(/2026/);
  });
});

describe("status tones never flatter an unfinished state", () => {
  it("paints only an active domain green", () => {
    expect(domainTone("active")).toBe("ok");
    expect(domainTone("issuing_cert")).toBe("info");
    expect(domainTone("pending_dns")).toBe("warn");
    expect(domainTone("failed")).toBe("bad");
    expect(domainTone("disabled")).toBe("neutral");
    expect(domainTone("who_knows")).toBe("neutral");
  });

  it("words the domain states the API uses", () => {
    expect(domainStatusLabel("pending_dns")).toBe("Waiting for DNS");
    expect(domainStatusLabel("active")).toBe("Live");
    expect(domainStatusLabel("weird")).toBe("weird");
  });

  it("separates a draft rota from a published one", () => {
    expect(scheduleTone("published")).toBe("ok");
    expect(scheduleTone("draft")).toBe("warn");
    expect(scheduleTone("archived")).toBe("neutral");
  });

  it("treats a dropped delivery as a warning, not a success", () => {
    expect(deliveryTone("sent")).toBe("ok");
    expect(deliveryTone("queued")).toBe("info");
    expect(deliveryTone("dropped")).toBe("warn");
    expect(deliveryTone("failed")).toBe("bad");
  });

  it("tones time-off and certificate states", () => {
    expect(timeOffTone("approved")).toBe("ok");
    expect(timeOffTone("requested")).toBe("warn");
    expect(certificateTone("issued", false)).toBe("ok");
    expect(certificateTone("issued", true)).toBe("warn");
    expect(certificateTone("expired", false)).toBe("bad");
    expect(certificateTone("pending", false)).toBe("info");
  });
});

describe("worstDomainStatus ranks by what an operator must act on", () => {
  it("reports no domains as its own state", () => {
    expect(worstDomainStatus([])).toBe("none");
  });

  it("lets a failure outrank everything", () => {
    expect(worstDomainStatus(["active", "failed", "pending_dns"])).toBe("failed");
  });

  it("notices a domain still waiting", () => {
    expect(worstDomainStatus(["active", "pending_dns"])).toBe("pending");
    expect(worstDomainStatus(["issuing_cert"])).toBe("pending");
  });

  it("only reports active when every domain is live", () => {
    expect(worstDomainStatus(["active", "active"])).toBe("active");
    expect(worstDomainStatus(["disabled"])).toBe("pending");
  });
});

describe("coverageVerdict", () => {
  it("says nothing was solved when there is no ratio", () => {
    expect(coverageVerdict(null)).toEqual({ tone: "neutral", label: "Not solved yet" });
  });

  it("separates full coverage from near coverage", () => {
    expect(coverageVerdict(1)).toEqual({ tone: "ok", label: "Fully covered" });
    expect(coverageVerdict(0.97)).toEqual({ tone: "warn", label: "Nearly covered" });
    expect(coverageVerdict(0.62)).toEqual({ tone: "bad", label: "Short-staffed" });
  });
});

describe("summariseDaily", () => {
  const totals = { "2026-10-07": 40, "2026-10-05": 100, "2026-10-06": 0 };

  it("sorts days, finds the peak and keeps an honest mean", () => {
    const summary = summariseDaily(totals);
    expect(summary.days.map((day) => day.date)).toEqual([
      "2026-10-05",
      "2026-10-06",
      "2026-10-07",
    ]);
    expect(summary.peak).toEqual({ date: "2026-10-05", expected: 100 });
    expect(summary.total).toBe(140);
    expect(summary.mean).toBeCloseTo(140 / 3, 6);
  });

  it("handles an empty or missing set without throwing", () => {
    expect(summariseDaily({})).toEqual({ days: [], peak: null, mean: null, total: null });
    expect(summariseDaily(null)).toEqual({ days: [], peak: null, mean: null, total: null });
  });

  it("drops non-finite values instead of turning them into bars", () => {
    const summary = summariseDaily({ "2026-10-05": Number.NaN, "2026-10-06": 10 });
    expect(summary.days).toHaveLength(1);
    expect(summary.peak?.date).toBe("2026-10-06");
  });
});

describe("barWidthPercent", () => {
  it("scales against the busiest day", () => {
    expect(barWidthPercent(100, 100)).toBe(100);
    expect(barWidthPercent(50, 100)).toBe(50);
    expect(barWidthPercent(1, 3)).toBe(33);
  });

  it("draws nothing for a closed day", () => {
    // A minimum sliver would read as real demand at 03:00.
    expect(barWidthPercent(0, 100)).toBe(0);
    expect(barWidthPercent(10, 0)).toBe(0);
    expect(barWidthPercent(Number.NaN, 100)).toBe(0);
  });
});

describe("paidMinutes and templateViolations", () => {
  it("takes only the unpaid break off the length", () => {
    expect(paidMinutes({ length_minutes: 480, unpaid_break_minutes: 30 })).toBe(450);
    expect(paidMinutes({ length_minutes: 480, unpaid_break_minutes: 0 })).toBe(480);
    expect(paidMinutes({ length_minutes: 60, unpaid_break_minutes: 90 })).toBe(0);
  });

  it("flags a template longer than the shift cap", () => {
    const rules = { max_shift_hours: 8 };
    expect(templateViolations({ name: "Day", length_minutes: 480, unpaid_break_minutes: 30 }, rules)).toEqual([]);
    const problems = templateViolations(
      { name: "Long day", length_minutes: 660, unpaid_break_minutes: 30 },
      rules,
    );
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("Long day");
    expect(problems[0]).toContain("10h 30m");
  });

  it("reports nothing when there is no rule set to violate", () => {
    expect(templateViolations({ name: "x", length_minutes: 1000, unpaid_break_minutes: 0 }, null)).toEqual([]);
  });
});

describe("widget and delivery helpers", () => {
  it("labels a quiet-hours window and its absence", () => {
    expect(quietHoursLabel({ start_minute: 1320, end_minute: 420 })).toBe("22:00–07:00");
    expect(quietHoursLabel({})).toBe("Not set");
    expect(quietHoursLabel(null)).toBe("Not set");
    expect(quietHoursLabel({ start_minute: 1320 })).toBe("Not set");
  });

  it("orders the delivery summary the way the platform states them", () => {
    expect(deliverySummaryLabel({ sent: 3, dropped: 1 })).toBe("3 sent · 1 dropped");
    expect(deliverySummaryLabel({ sent: 0, queued: 2 })).toBe("2 queued");
    expect(deliverySummaryLabel({})).toBe("No deliveries");
    expect(deliverySummaryLabel(null)).toBe("No deliveries");
  });

  it("groups shifts by tenant-local day in date order", () => {
    const grouped = shiftsByDay([
      { starts_at_local: "2026-10-06T09:00:00+06:00" },
      { starts_at_local: "2026-10-05T13:00:00+06:00" },
      { starts_at_local: "2026-10-05T09:00:00+06:00" },
    ]);
    expect(grouped).toEqual([
      { date: "2026-10-05", count: 2 },
      { date: "2026-10-06", count: 1 },
    ]);
  });

  it("de-duplicates skill keys", () => {
    expect(
      skillKeys([{ required_skill_key: "voice" }, { required_skill_key: "voice" }, { required_skill_key: "" }]),
    ).toEqual(["voice"]);
  });
});

describe("contrastFailures", () => {
  it("prefers the server's own failure strings", () => {
    const report = {
      pairs: [],
      failures: ["text on background is 4.478:1, needs 4.5:1"],
    };
    expect(contrastFailures(report)).toEqual(["text on background is 4.478:1, needs 4.5:1"]);
  });

  it("derives failures from the pairs when the list is absent", () => {
    const report = {
      pairs: [
        { foreground: "text", background: "background", ratio: 4.478, minimum: 4.5, passed: false },
        { foreground: "accent", background: "background", ratio: 6.1, minimum: 3, passed: true },
      ],
    };
    const failures = contrastFailures(report);
    expect(failures).toHaveLength(1);
    expect(failures[0]).toContain("4.48:1");
  });

  it("reports nothing for a missing report", () => {
    expect(contrastFailures(null)).toEqual([]);
  });
});
