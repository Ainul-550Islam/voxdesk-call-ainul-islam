"use client";

import { useCallback, useState } from "react";
import { api, errorMessage } from "../lib/api";

export type OccupancyRow = { user_id: string; seconds: Record<string, number>; occupancy: string };

export function useOccupancy() {
  const [rows, setRows] = useState<OccupancyRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async (start: string, end: string, userId?: string) => {
    setLoading(true);
    try {
      const query = new URLSearchParams({ start, end });
      if (userId) query.set("user_id", userId);
      const result = await api<{ agents: OccupancyRow[] }>(`/api/cc2/supervisor/occupancy?${query.toString()}`);
      setRows(result.agents);
      setError("");
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setLoading(false); }
  }, []);
  return { rows, loading, error, load };
}
