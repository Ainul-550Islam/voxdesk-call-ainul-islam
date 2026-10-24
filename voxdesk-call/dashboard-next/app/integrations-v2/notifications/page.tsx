"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  int2Api,
  type ConnectionMasked,
  type NotificationRuleItem,
} from "../lib/api";

export default function CollabNotificationsPage() {
  const [connections, setConnections] = useState<ConnectionMasked[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [rules, setRules] = useState<NotificationRuleItem[]>([]);

  const [connectionId, setConnectionId] = useState("");
  const [eventType, setEventType] = useState("escalation");
  const [teamId, setTeamId] = useState("");
  const [channelId, setChannelId] = useState("");
  const [template, setTemplate] = useState(
    "[{{event_type}}] {{title}} — {{summary}}"
  );

  const [testResult, setTestResult] = useState("");
  const [error, setError] = useState("");

  const loadData = useCallback(async () => {
    setError("");
    try {
      const [connRes, ruleRes] = await Promise.all([
        int2Api.listConnections("collab"),
        int2Api.listNotificationRules(),
      ]);
      setConnections(connRes.connections);
      setEvents(ruleRes.supported_events);
      setRules(ruleRes.rules);
      if (connRes.connections[0] && !connectionId) {
        setConnectionId(connRes.connections[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load rules");
    }
  }, [connectionId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const handleSaveRule = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setTestResult("");
    try {
      const res = await int2Api.saveNotificationRule({
        connection_id: connectionId,
        event_type: eventType,
        team_id: teamId,
        channel_id: channelId,
        template,
        enabled: true,
      });
      setRules((prev) => [
        res.rule,
        ...prev.filter((r) => r.id !== res.rule.id),
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save rule");
    }
  };

  const handleSendTest = async (rule: NotificationRuleItem) => {
    setError("");
    setTestResult("");
    try {
      const res = await int2Api.sendTestNotification({
        rule_id: rule.id,
        title: "VoxDesk Notification Rule Test",
        summary: `Live delivery check for ${rule.event_type}`,
      });
      setTestResult(
        `Delivered via ${res.provider} (${res.status}) — message_id: ${res.message_id || "n/a"}`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Test delivery failed");
    }
  };

  const handleDelete = async (ruleId: string) => {
    setError("");
    try {
      await int2Api.deleteNotificationRule(ruleId);
      setRules((prev) => prev.filter((r) => r.id !== ruleId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  return (
    <main style={{ maxWidth: 980, margin: "0 auto", padding: "1.75rem" }}>
      <div style={{ marginBottom: "1rem" }}>
        <Link href="/integrations-v2">← Back to Integrations Catalog</Link>
      </div>
      <h1 style={{ marginTop: 0 }}>Collaboration Notification Rules</h1>

      {error && (
        <div
          role="alert"
          style={{
            padding: "0.75rem 1rem",
            background: "#fef2f2",
            color: "#991b1b",
            borderRadius: 8,
            marginBottom: "1rem",
          }}
        >
          {error}
        </div>
      )}
      {testResult && (
        <div
          data-testid="notify-test-result"
          style={{
            padding: "0.75rem 1rem",
            background: "#ecfdf5",
            color: "#065f46",
            borderRadius: 8,
            marginBottom: "1rem",
          }}
        >
          {testResult}
        </div>
      )}

      <form
        onSubmit={handleSaveRule}
        style={{
          border: "1px solid #e2e8f0",
          borderRadius: 10,
          padding: "1rem",
          marginBottom: "1.5rem",
        }}
      >
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
          <label style={{ fontSize: "0.84rem" }}>
            Connection
            <select
              required
              value={connectionId}
              onChange={(e) => setConnectionId(e.target.value)}
              style={{ display: "block", width: "100%", padding: "0.4rem", marginTop: 4 }}
            >
              <option value="">Select Slack / Teams / Zoom connection…</option>
              {connections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.display_name} ({c.provider})
                </option>
              ))}
            </select>
          </label>

          <label style={{ fontSize: "0.84rem" }}>
            Event Trigger
            <select
              value={eventType}
              onChange={(e) => setEventType(e.target.value)}
              style={{ display: "block", width: "100%", padding: "0.4rem", marginTop: 4 }}
            >
              {(events.length > 0
                ? events
                : ["missed_call", "escalation", "voicemail", "qa_flag", "sla_breach"]
              ).map((ev) => (
                <option key={ev} value={ev}>
                  {ev}
                </option>
              ))}
            </select>
          </label>

          <label style={{ fontSize: "0.84rem" }}>
            Channel ID
            <input
              required
              value={channelId}
              onChange={(e) => setChannelId(e.target.value)}
              placeholder="C0123456789"
              style={{ display: "block", width: "100%", padding: "0.4rem", marginTop: 4 }}
            />
          </label>

          <label style={{ fontSize: "0.84rem" }}>
            Teams ID (required for Microsoft Teams)
            <input
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              placeholder="Optional for Slack"
              style={{ display: "block", width: "100%", padding: "0.4rem", marginTop: 4 }}
            />
          </label>
        </div>

        <label style={{ display: "block", fontSize: "0.84rem", marginTop: "0.75rem" }}>
          Safe Message Template ({"{{variable}}"} placeholders only)
          <input
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
            style={{ display: "block", width: "100%", padding: "0.4rem", marginTop: 4 }}
          />
        </label>

        <button
          type="submit"
          style={{
            marginTop: "0.85rem",
            padding: "0.45rem 0.9rem",
            borderRadius: 6,
            border: "none",
            background: "#2563eb",
            color: "#ffffff",
            fontWeight: 600,
          }}
        >
          Save Notification Rule
        </button>
      </form>

      <h2 style={{ fontSize: "1.1rem" }}>Active Rules</h2>
      {rules.length === 0 ? (
        <p style={{ color: "#64748b" }}>No notification rules configured.</p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #cbd5e1" }}>
              <th style={{ padding: "0.45rem" }}>Event</th>
              <th style={{ padding: "0.45rem" }}>Channel</th>
              <th style={{ padding: "0.45rem" }}>Template</th>
              <th style={{ padding: "0.45rem" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ padding: "0.45rem", fontWeight: 600 }}>{r.event_type}</td>
                <td style={{ padding: "0.45rem" }}>{r.channel_id}</td>
                <td style={{ padding: "0.45rem", fontFamily: "monospace" }}>
                  {r.template || "(default card)"}
                </td>
                <td style={{ padding: "0.45rem", display: "flex", gap: "0.5rem" }}>
                  <button type="button" onClick={() => void handleSendTest(r)}>
                    Send Test
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDelete(r.id)}
                    style={{ color: "#991b1b" }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
