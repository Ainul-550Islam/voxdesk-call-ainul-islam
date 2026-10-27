"use client";

import { useState } from "react";

import { Callout, Empty, Failure, Loading, Metric, Panel, StatusPill } from "../components/Primitives";
import { plat2Api } from "../lib/api";
import type { ScheduleDetail, ScheduleRow, SolveReport, TimeOffRow } from "../lib/api";
import {
  coverageVerdict,
  durationLabel,
  formatNumber,
  formatPercent,
  scheduleTone,
  shiftsByDay,
  timeOffTone,
  timestampLabel,
} from "../lib/format";
import { useApi } from "../lib/useApi";
import styles from "../platform-admin.module.css";

/**
 * Schedules: solve a week, inspect the coverage the solver actually achieved, publish a
 * specific version, then clear the time-off queue.
 *
 * The publish button sends the version id it was rendered with. Publishing "whatever is
 * current" from a button that was drawn ten minutes ago is how an operator publishes a
 * rota they never looked at.
 */
export default function SchedulePage() {
  const schedules = useApi<{ schedules: ScheduleRow[] }>(() => plat2Api.listSchedules(20), []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [report, setReport] = useState<SolveReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [employeeIds, setEmployeeIds] = useState("");
  const [templateIds, setTemplateIds] = useState("");
  const [allowShortfall, setAllowShortfall] = useState(true);

  const detail = useApi<ScheduleDetail | null>(
    () => (selectedId ? plat2Api.readSchedule(selectedId) : Promise.resolve(null)),
    [selectedId],
  );
  const timeOff = useApi<{ requests: TimeOffRow[] }>(
    () => plat2Api.listTimeOff(["requested"]),
    [],
  );

  const rows = schedules.data?.schedules ?? [];
  const current = detail.data?.shifts ?? [];
  const latestVersion = detail.data?.versions?.[0] ?? null;

  async function solve() {
    if (!selectedId) return;
    setBusy("solve");
    setActionError(null);
    setNotice(null);
    try {
      const result = await plat2Api.solveSchedule(selectedId, {
        schedule_id: selectedId,
        employee_ids: employeeIds
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        template_ids: templateIds
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        auto_record: true,
        allow_shortfall: allowShortfall,
      });
      setReport(result);
      setNotice(
        `Solver finished with status ${result.status}; ${result.assignments} shifts recorded as a new version.`,
      );
      detail.reload();
      schedules.reload();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function publish(versionId: string) {
    if (!selectedId) return;
    setBusy("publish");
    setActionError(null);
    setNotice(null);
    try {
      const result = await plat2Api.publishSchedule(selectedId, { version_id: versionId });
      setNotice(
        `Published version ${result.version_number}; ${result.notifications.length} people have changes to be told about.`,
      );
      detail.reload();
      schedules.reload();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function decideTimeOff(row: TimeOffRow, approved: boolean) {
    setBusy(row.id);
    setActionError(null);
    try {
      await plat2Api.decideTimeOff(row.id, { approved });
      timeOff.reload();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  const verdict = coverageVerdict(report ? report.coverage_ratio : null);

  return (
    <>
      <h1 className={styles.pageTitle}>Schedules</h1>
      <p className={styles.pageLead}>
        Solve a week against a forecast-derived requirement curve, review the coverage the
        solver reports, then publish a numbered version. Nothing becomes visible to an
        employee until a version is published.
      </p>

      <Panel title="Weeks" subtitle="One row per schedule week; select one to work on it.">
        {schedules.loading ? <Loading what="schedules" /> : null}
        {schedules.error ? (
          <Failure what="schedules" error={schedules.error} onRetry={schedules.reload} />
        ) : null}
        {schedules.data && rows.length === 0 ? (
          <Empty>No schedule exists for this tenant yet.</Empty>
        ) : null}
        {rows.length > 0 ? (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Week start</th>
                <th>Name</th>
                <th>Timezone</th>
                <th>Status</th>
                <th>Version</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.week_start}</td>
                  <td>{row.name}</td>
                  <td className={styles.mono}>{row.timezone}</td>
                  <td>
                    <StatusPill tone={scheduleTone(row.status)} label={row.status} />
                  </td>
                  <td className={styles.mono}>{row.current_version_id ? "current" : "none"}</td>
                  <td>
                    <button
                      type="button"
                      className={styles.button}
                      onClick={() => {
                        setSelectedId(row.id);
                        setReport(null);
                        setNotice(null);
                      }}
                    >
                      {selectedId === row.id ? "Selected" : "Open"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Panel>

      {selectedId ? (
        <Panel
          title="Solve"
          subtitle="Employee and template ids are comma separated. Templates must be active; employees come from the tenant roster."
        >
          <div className={styles.inlineForm}>
            <label className={styles.field}>
              Employee ids
              <input
                value={employeeIds}
                onChange={(event) => setEmployeeIds(event.target.value)}
                placeholder="uuid, uuid, …"
              />
            </label>
            <label className={styles.field}>
              Template ids
              <input
                value={templateIds}
                onChange={(event) => setTemplateIds(event.target.value)}
                placeholder="uuid, uuid, …"
              />
            </label>
            <label className={styles.field}>
              Shortfall
              <select
                value={allowShortfall ? "allow" : "hard"}
                onChange={(event) => setAllowShortfall(event.target.value === "allow")}
              >
                <option value="allow">Allow, minimise</option>
                <option value="hard">Refuse if uncovered</option>
              </select>
            </label>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              onClick={solve}
              disabled={busy !== null}
            >
              {busy === "solve" ? "Solving…" : "Solve week"}
            </button>
          </div>
          {actionError ? <Callout tone="bad">{actionError}</Callout> : null}
          {notice ? <Callout tone="ok">{notice}</Callout> : null}

          {report ? (
            <>
              <div className={styles.grid}>
                <Metric label="Solver status" value={report.status} hint={`${report.intervals} intervals`} />
                <Metric
                  label="Coverage"
                  value={formatPercent(report.coverage_ratio)}
                  hint={verdict.label}
                />
                <Metric
                  label="Shifts recorded"
                  value={String(report.assignments)}
                  hint={`objective ${formatNumber(report.objective, 2)}`}
                />
                <Metric
                  label="Shortfall intervals"
                  value={String(report.shortfall_agent_intervals)}
                  hint={`over-coverage ${report.overcoverage_agent_intervals}`}
                />
              </div>
              <StatusPill tone={verdict.tone} label={verdict.label} />
              <Callout tone="info">
                A coverage below 100% is reported as the count of agent-intervals the rota
                leaves unstaffed. It is not rounded up to “covered”.
              </Callout>
            </>
          ) : null}
        </Panel>
      ) : null}

      {detail.data ? (
        <>
          <Panel title="Published shifts" subtitle={`${current.length} shifts in the current version.`}>
            {detail.loading ? <Loading what="schedule" /> : null}
            {detail.error ? (
              <Failure what="schedule" error={detail.error} onRetry={detail.reload} />
            ) : null}
            {current.length === 0 ? (
              <Empty>This schedule has no shifts in its current version.</Empty>
            ) : null}
            {current.length > 0 ? (
              <>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Start (local)</th>
                      <th>End (local)</th>
                      <th>Paid</th>
                      <th>Skill</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.slice(0, 40).map((shift) => (
                      <tr key={shift.shift_id}>
                        <td>{shift.starts_at_local}</td>
                        <td>{shift.ends_at_local}</td>
                        <td className={styles.numeric}>{durationLabel(shift.paid_hours * 60)}</td>
                        <td>{shift.skill_key || "—"}</td>
                        <td>{shift.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Callout tone="neutral">
                  Showing {Math.min(40, current.length)} of {current.length} shifts. Per-day
                  count:{" "}
                  {shiftsByDay(current)
                    .map((day) => `${day.date} × ${day.count}`)
                    .join(" · ")}
                </Callout>
              </>
            ) : null}
          </Panel>

          <Panel title="Versions" subtitle="Publishing supersedes the previous version; nothing is deleted.">
            {(detail.data.versions ?? []).length === 0 ? (
              <Empty>No version has been recorded yet — solve the week first.</Empty>
            ) : (
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>Status</th>
                    <th>Shifts</th>
                    <th>Coverage</th>
                    <th>Created</th>
                    <th>Published</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(detail.data.versions ?? []).map((version) => (
                    <tr key={version.version_id}>
                      <td className={styles.numeric}>#{version.version_number}</td>
                      <td>{version.status}</td>
                      <td className={styles.numeric}>{version.assignments}</td>
                      <td className={styles.numeric}>
                        {formatPercent(version.solver.coverage_ratio ?? null)}
                      </td>
                      <td>{timestampLabel(version.created_at)}</td>
                      <td>{version.published_at ? timestampLabel(version.published_at) : "Not yet"}</td>
                      <td>
                        <button
                          type="button"
                          className={styles.button}
                          onClick={() => publish(version.version_id)}
                          disabled={busy !== null || version.status === "published"}
                        >
                          {version.status === "published" ? "Published" : "Publish"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </>
      ) : null}

      <Panel
        title="Time off to approve"
        subtitle="Approving removes the person from the rota windows the request covers on the next solve."
      >
        {timeOff.loading ? <Loading what="time-off requests" /> : null}
        {timeOff.error ? (
          <Failure what="time-off requests" error={timeOff.error} onRetry={timeOff.reload} />
        ) : null}
        {timeOff.data && timeOff.data.requests.length === 0 ? (
          <Empty>No pending request.</Empty>
        ) : null}
        {(timeOff.data?.requests ?? []).length > 0 ? (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Kind</th>
                <th>From</th>
                <th>To</th>
                <th>Hours</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(timeOff.data?.requests ?? []).map((row) => (
                <tr key={row.id}>
                  <td>{row.kind}</td>
                  <td>{timestampLabel(row.starts_at)}</td>
                  <td>{timestampLabel(row.ends_at)}</td>
                  <td className={styles.numeric}>{formatNumber(row.hours, 1)}</td>
                  <td>
                    <StatusPill tone={timeOffTone(row.status)} label={row.status} />
                  </td>
                  <td>
                    <button
                      type="button"
                      className={styles.button}
                      onClick={() => decideTimeOff(row, true)}
                      disabled={busy !== null}
                    >
                      Approve
                    </button>{" "}
                    <button
                      type="button"
                      className={styles.button}
                      onClick={() => decideTimeOff(row, false)}
                      disabled={busy !== null}
                    >
                      Deny
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </Panel>
    </>
  );
}
