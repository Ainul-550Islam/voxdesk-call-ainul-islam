"use client";

import { SupervisorWallboard } from "../../components/supervisor/SupervisorWallboard";
import { useWallboard } from "../agent-desktop/hooks/useWallboard";

export default function SupervisorPage() {
  const { snapshot, loading, error, transport, reload } = useWallboard();
  return <SupervisorWallboard snapshot={snapshot} loading={loading} error={error} transport={transport} onRefresh={() => void reload()} />;
}
