"use client";

/**
 * Daily expected-volume bars.
 *
 * A bar chart, not a sparkline with a smoothed curve: the only claim it makes is
 * "this is the number the model stored for this day", and a bar can be read exactly.
 * Zero-volume days render as a hollow track so a closed day cannot be mistaken for a
 * small amount of demand.
 */

import { barWidthPercent, dayLabel, formatNumber, summariseDaily } from "../lib/format";
import styles from "../platform-admin.module.css";

export function DailyVolumeBars({
  totals,
  unitLabel = "interactions",
}: {
  totals: Record<string, number> | null | undefined;
  unitLabel?: string;
}) {
  const summary = summariseDaily(totals);
  if (summary.days.length === 0) {
    return <p className={styles.state}>No daily totals were stored for this forecast.</p>;
  }
  const peak = summary.peak?.expected ?? 0;

  return (
    <div className={styles.bars} role="img" aria-label={`Expected ${unitLabel} per day`}>
      {summary.days.map((day) => {
        const width = barWidthPercent(day.expected, peak);
        return (
          <div className={styles.barRow} key={day.date}>
            <span>{dayLabel(day.date)}</span>
            <span className={styles.barTrack}>
              <span
                className={day.expected > 0 ? styles.barFill : styles.barZero}
                style={{ display: "block", width: `${width}%` }}
                data-width={width}
              />
            </span>
            <span className={`${styles.numeric} ${styles.mono}`}>
              {formatNumber(day.expected)}
            </span>
          </div>
        );
      })}
      <p className={styles.state}>
        Peak {formatNumber(summary.peak?.expected ?? 0)} on {dayLabel(summary.peak?.date ?? "")} ·
        mean {formatNumber(summary.mean)} {unitLabel} per day over {summary.days.length} days.
      </p>
    </div>
  );
}

export default DailyVolumeBars;
