import React, { useCallback, useEffect, useState } from 'react';
import {
  completeExperiment,
  createExperiment,
  deleteExperiment,
  type Experiment,
  type ExperimentResultsResponse,
  getExperimentResults,
  listExperiments,
  pauseExperiment,
  promoteExperiment,
  rollbackExperiment,
  startExperiment,
} from '../../api/ab-testing';

export function ExperimentsPage() {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [results, setResults] = useState<ExperimentResultsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [newName, setNewName] = useState('');
  const [newAgentId, setNewAgentId] = useState('');
  const [controlWeight, setControlWeight] = useState(50);
  const [challengerWeight, setChallengerWeight] = useState(50);

  const refreshList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listExperiments();
      setExperiments(res.experiments || []);
      if (!selectedId && res.experiments?.length) {
        setSelectedId(res.experiments[0].id);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load experiments');
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  useEffect(() => {
    if (!selectedId) {
      setResults(null);
      return;
    }
    let cancelled = false;
    getExperimentResults(selectedId)
      .then((res) => {
        if (!cancelled) setResults(res);
      })
      .catch(() => {
        if (!cancelled) setResults(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, experiments]);

  const selected = experiments.find((exp) => exp.id === selectedId) || null;

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim() || !newAgentId.trim()) {
      setError('Both experiment name and target Agent ID are required.');
      return;
    }
    setBusyAction('create');
    setError(null);
    setNotice(null);
    try {
      const created = await createExperiment({
        name: newName.trim(),
        agent_id: newAgentId.trim() || null,
        variants: [
          { name: 'Control', weight: Number(controlWeight), is_control: true },
          { name: 'Variant B', weight: Number(challengerWeight), is_control: false },
        ],
      });
      setNewName('');
      setNewAgentId('');
      setSelectedId(created.id);
      setNotice(`Created experiment "${created.name}".`);
      await refreshList();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not create experiment');
    } finally {
      setBusyAction(null);
    }
  };

  const runAction = async (label: string, fn: () => Promise<unknown>) => {
    setBusyAction(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(`Action "${label}" succeeded.`);
      await refreshList();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : `Action "${label}" failed`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-8 text-slate-100">
      <div className="mx-auto max-w-6xl space-y-8">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-white">
              A/B Prompt &amp; Voice Experiments
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              Run deterministic traffic splits across immutable AgentVersions, evaluate statistical lift, and promote or roll back safely.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void refreshList()}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
          >
            Refresh
          </button>
        </header>

        {error && (
          <div role="alert" className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
            {error}
          </div>
        )}
        {notice && (
          <div role="status" className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-200">
            {notice}
          </div>
        )}

        <section aria-labelledby="create-experiment-heading" className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
          <h2 id="create-experiment-heading" className="text-base font-medium text-white">
            Create New Experiment
          </h2>
          <form onSubmit={handleCreate} className="mt-4 grid gap-4 sm:grid-cols-5">
            <label className="flex flex-col gap-1 text-xs text-slate-300 sm:col-span-2">
              Experiment Name
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Greeting Conciseness Test"
                className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-slate-300">
              Agent ID (UUID)
              <input
                type="text"
                value={newAgentId}
                onChange={(e) => setNewAgentId(e.target.value)}
                placeholder="Optional agent UUID"
                className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-slate-300">
              Control / Challenger %
              <div className="flex gap-2">
                <input
                  type="number"
                  aria-label="Control weight"
                  value={controlWeight}
                  onChange={(e) => setControlWeight(Number(e.target.value))}
                  className="w-1/2 rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-white"
                />
                <input
                  type="number"
                  aria-label="Challenger weight"
                  value={challengerWeight}
                  onChange={(e) => setChallengerWeight(Number(e.target.value))}
                  className="w-1/2 rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-white"
                />
              </div>
            </label>
            <div className="flex items-end">
              <button
                type="submit"
                disabled={busyAction !== null}
                className="w-full rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
              >
                Create Experiment
              </button>
            </div>
          </form>
        </section>

        <div className="grid gap-6 lg:grid-cols-3">
          <section aria-labelledby="experiments-list-heading" className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
            <h2 id="experiments-list-heading" className="text-sm font-medium text-slate-200">
              Experiments ({experiments.length})
            </h2>
            {loading ? (
              <p className="mt-4 text-xs text-slate-400">Loading experiments…</p>
            ) : experiments.length === 0 ? (
              <p className="mt-4 text-xs text-slate-400">No experiments created yet.</p>
            ) : (
              <ul className="mt-4 space-y-2">
                {experiments.map((exp) => (
                  <li key={exp.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(exp.id)}
                      className={`w-full rounded-xl border px-3 py-2.5 text-left transition ${
                        exp.id === selectedId
                          ? 'border-blue-500/60 bg-blue-500/10'
                          : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium text-white">{exp.name}</span>
                        <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] uppercase text-slate-300">
                          {exp.status}
                        </span>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 lg:col-span-2">
            {!selected ? (
              <p className="text-sm text-slate-400">Select an experiment to inspect metrics and lifecycle controls.</p>
            ) : (
              <div className="space-y-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <h2 className="text-lg font-semibold text-white">{selected.name}</h2>
                    <p className="text-xs text-slate-400">
                      Status: <strong className="text-slate-200">{selected.status}</strong> · Metric:{' '}
                      {selected.success_metric} · Agent: {selected.agent_id || 'Unbound'}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {(selected.status === 'draft' || selected.status === 'paused') && (
                      <button
                        type="button"
                        disabled={busyAction !== null}
                        onClick={() => void runAction('start', () => startExperiment(selected.id))}
                        className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500"
                      >
                        Start
                      </button>
                    )}
                    {selected.status === 'running' && (
                      <>
                        <button
                          type="button"
                          disabled={busyAction !== null}
                          onClick={() => void runAction('pause', () => pauseExperiment(selected.id))}
                          className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-500"
                        >
                          Pause
                        </button>
                        <button
                          type="button"
                          disabled={busyAction !== null}
                          onClick={() => void runAction('complete', () => completeExperiment(selected.id))}
                          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500"
                        >
                          Complete
                        </button>
                      </>
                    )}
                    {selected.status === 'promoted' && (
                      <button
                        type="button"
                        disabled={busyAction !== null}
                        onClick={() => void runAction('rollback', () => rollbackExperiment(selected.id))}
                        className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-500"
                      >
                        Rollback Promotion
                      </button>
                    )}
                    {selected.status !== 'running' && (
                      <button
                        type="button"
                        disabled={busyAction !== null}
                        onClick={() =>
                          void runAction('delete', async () => {
                            await deleteExperiment(selected.id);
                            setSelectedId(null);
                          })
                        }
                        className="rounded-lg border border-red-500/40 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/10"
                      >
                        Delete
                      </button>
                    )}
                  </div>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="border-b border-slate-800 text-slate-400">
                      <tr>
                        <th className="py-2 pr-3">Variant</th>
                        <th className="py-2 pr-3">Weight</th>
                        <th className="py-2 pr-3">Impressions</th>
                        <th className="py-2 pr-3">Conversions</th>
                        <th className="py-2 pr-3">Conv. Rate</th>
                        <th className="py-2 pr-3">p-value</th>
                        <th className="py-2">Promote</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/70">
                      {selected.variants.map((v) => {
                        const stat = results?.variants.find((rv) => rv.variant_id === v.id);
                        return (
                          <tr key={v.id}>
                            <td className="py-2.5 pr-3 font-medium text-white">
                              {v.name} {v.is_control ? '(Control)' : ''}
                            </td>
                            <td className="py-2.5 pr-3 text-slate-300">{v.weight}%</td>
                            <td className="py-2.5 pr-3 text-slate-300">{stat?.impressions ?? v.impressions}</td>
                            <td className="py-2.5 pr-3 text-slate-300">{stat?.conversions ?? v.conversions}</td>
                            <td className="py-2.5 pr-3 text-slate-300">
                              {(((stat?.conversion_rate ?? v.conversion_rate) || 0) * 100).toFixed(1)}%
                            </td>
                            <td className="py-2.5 pr-3 text-slate-300">
                              {stat?.p_value != null ? stat.p_value.toFixed(4) : '—'}
                            </td>
                            <td className="py-2.5">
                              {selected.agent_id && selected.status !== 'promoted' && (
                                <button
                                  type="button"
                                  disabled={busyAction !== null}
                                  onClick={() =>
                                    void runAction(`promote:${v.name}`, () =>
                                      promoteExperiment(selected.id, v.id),
                                    )
                                  }
                                  className="rounded border border-blue-500/40 px-2.5 py-1 text-[11px] font-medium text-blue-300 hover:bg-blue-500/10"
                                >
                                  Promote {v.name}
                                </button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}

export default ExperimentsPage;
