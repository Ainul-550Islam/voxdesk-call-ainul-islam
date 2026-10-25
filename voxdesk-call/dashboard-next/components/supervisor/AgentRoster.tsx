"use client";

import type { WallboardAgent } from "../../app/agent-desktop/types";
import styles from "../../app/agent-desktop/agent-desktop.module.css";

export function AgentRoster({ agents }: { agents: WallboardAgent[] }) {
  return <section className={styles.card} aria-label="Agent roster"><h2>Agents</h2><div style={{ overflowX: "auto" }}><table><thead><tr><th>Agent</th><th>State</th><th>Occupancy</th><th>Softphone</th></tr></thead><tbody>{agents.map((agent) => <tr key={agent.user_id}><td>{agent.full_name}</td><td>{agent.state}</td><td>{agent.active_count}/{agent.capacity}</td><td>{agent.softphone_registered ? "Registered" : "Not registered"}</td></tr>)}</tbody></table></div>{!agents.length && <p className={styles.empty}>No agent presence rows are available.</p>}</section>;
}
