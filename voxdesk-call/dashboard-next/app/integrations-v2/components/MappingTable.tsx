"use client";

import React from "react";
import type { FieldMappingRuleRow } from "../lib/api";

export interface MappingValidationError {
  field: string;
  message: string;
}

export interface MappingTableProps {
  rules: FieldMappingRuleRow[];
  transforms: string[];
  validationErrors?: MappingValidationError[];
  onChange: (next: FieldMappingRuleRow[]) => void;
}

export function MappingTable({
  rules,
  transforms,
  validationErrors = [],
  onChange,
}: MappingTableProps) {
  const updateRow = (idx: number, patch: Partial<FieldMappingRuleRow>) => {
    const next = rules.map((r, i) => (i === idx ? { ...r, ...patch } : r));
    onChange(next);
  };

  const removeRow = (idx: number) => {
    onChange(rules.filter((_, i) => i !== idx));
  };

  const addRow = () => {
    onChange([
      ...rules,
      {
        entity_type: rules[0]?.entity_type || "Contact",
        direction: "bidirectional",
        voxdesk_field: "",
        external_field: "",
        transform: transforms[0] || "identity",
        transform_config: {},
        required: false,
        default_value: null,
      },
    ]);
  };

  const localIssues: MappingValidationError[] = [];
  rules.forEach((r, idx) => {
    if (!r.voxdesk_field.trim() || !r.external_field.trim()) {
      localIssues.push({
        field: `row_${idx + 1}`,
        message: `Row ${idx + 1}: both VoxDesk field and vendor field are required.`,
      });
    }
  });
  const combinedErrors = [...localIssues, ...validationErrors];

  return (
    <div>
      <table
        data-testid="mapping-table"
        style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}
      >
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #cbd5e1" }}>
            <th style={{ padding: "0.45rem" }}>VoxDesk Field</th>
            <th style={{ padding: "0.45rem" }}>External Field</th>
            <th style={{ padding: "0.45rem" }}>Direction</th>
            <th style={{ padding: "0.45rem" }}>Transform</th>
            <th style={{ padding: "0.45rem" }}>Required</th>
            <th style={{ padding: "0.45rem" }} />
          </tr>
        </thead>
        <tbody>
          {rules.map((r, idx) => (
            <tr key={idx} style={{ borderBottom: "1px solid #f1f5f9" }}>
              <td style={{ padding: "0.4rem" }}>
                <input
                  aria-label={`voxdesk-field-${idx}`}
                  value={r.voxdesk_field}
                  onChange={(e) => updateRow(idx, { voxdesk_field: e.target.value })}
                  placeholder="phone"
                  style={{ width: "100%", padding: "0.3rem" }}
                />
              </td>
              <td style={{ padding: "0.4rem" }}>
                <input
                  aria-label={`external-field-${idx}`}
                  value={r.external_field}
                  onChange={(e) => updateRow(idx, { external_field: e.target.value })}
                  placeholder="Phone"
                  style={{ width: "100%", padding: "0.3rem" }}
                />
              </td>
              <td style={{ padding: "0.4rem" }}>
                <select
                  aria-label={`direction-${idx}`}
                  value={r.direction}
                  onChange={(e) =>
                    updateRow(idx, {
                      direction: e.target.value as FieldMappingRuleRow["direction"],
                    })
                  }
                  style={{ padding: "0.3rem" }}
                >
                  <option value="bidirectional">bidirectional</option>
                  <option value="inbound">inbound</option>
                  <option value="outbound">outbound</option>
                </select>
              </td>
              <td style={{ padding: "0.4rem" }}>
                <select
                  aria-label={`transform-${idx}`}
                  value={r.transform}
                  onChange={(e) => updateRow(idx, { transform: e.target.value })}
                  style={{ padding: "0.3rem" }}
                >
                  {transforms.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </td>
              <td style={{ padding: "0.4rem" }}>
                <input
                  type="checkbox"
                  aria-label={`required-${idx}`}
                  checked={r.required}
                  onChange={(e) => updateRow(idx, { required: e.target.checked })}
                />
              </td>
              <td style={{ padding: "0.4rem" }}>
                <button
                  type="button"
                  onClick={() => removeRow(idx)}
                  style={{
                    background: "transparent",
                    border: "1px solid #cbd5e1",
                    borderRadius: 4,
                    padding: "0.2rem 0.5rem",
                    cursor: "pointer",
                  }}
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ marginTop: "0.6rem" }}>
        <button
          type="button"
          data-testid="add-mapping-row"
          onClick={addRow}
          style={{
            padding: "0.35rem 0.75rem",
            borderRadius: 6,
            border: "1px solid #cbd5e1",
            background: "#f8fafc",
            cursor: "pointer",
          }}
        >
          + Add Field Rule
        </button>
      </div>

      {combinedErrors.length > 0 && (
        <ul
          data-testid="mapping-validation-errors"
          style={{
            marginTop: "0.75rem",
            padding: "0.6rem 1.2rem",
            background: "#fef2f2",
            color: "#991b1b",
            borderRadius: 6,
            fontSize: "0.82rem",
          }}
        >
          {combinedErrors.map((err, i) => (
            <li key={`${err.field}-${i}`}>
              <strong>{err.field}:</strong> {err.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
