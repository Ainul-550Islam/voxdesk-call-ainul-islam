import type { WallboardAgent } from "../../../types";
import styles from "../../../agent-desktop.module.css";

export function AgentGrid({ agents }: { agents: WallboardAgent[] }) {
  return <section className={styles.panel} aria-labelledby="agent-grid-heading">
    <h2 id="agent-grid-heading">Agents</h2>
    {agents.length === 0 ? <p className={styles.muted}>No agents are present in this tenant.</p> : <table className={styles.table}>
      <thead><tr><th>Agent</th><th>State</th><th>Active / capacity</th><th>Softphone</th></tr></thead>
      <tbody>{agents.map((agent) => <tr key={agent.user_id}>
        <td><strong>{agent.full_name || agent.email}</strong><small className={styles.muted}> {agent.user_id}</small></td>
        <td>{agent.state.replaceAll("_", " ")}</td>
        <td>{agent.active_count} / {agent.capacity}</td>
        <td>{agent.softphone_registered ? "Registered" : "Not registered"}</td>
      </tr>)}</tbody>
    </table>}
  </section>;
}
