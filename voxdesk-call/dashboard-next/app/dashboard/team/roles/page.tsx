// File: dashboard-next/app/dashboard/team/roles/page.tsx — custom roles by module, assignment, preview
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, request } from "@/lib/api";

/**
 * Custom roles (PART 16, P16-01).
 *
 * Every byte on this page comes from `/api/auth/*` (app/api/role_routes.py):
 *   GET    /api/auth/permission-catalogue        permissions grouped by module
 *   GET    /api/auth/custom-roles                the tenant's roles
 *   POST   /api/auth/custom-roles                create
 *   PATCH  /api/auth/custom-roles/{id}           rename, retune, deactivate
 *   DELETE /api/auth/custom-roles/{id}           delete
 *   GET    /api/auth/custom-roles/{id}/assignments   who holds it
 *   POST   /api/auth/custom-roles/{id}/assignments   grant to user / service account / SCIM group
 *   DELETE /api/auth/custom-roles/{id}/assignments/{assignment_id}
 *   GET    /api/auth/effective-permissions?user_id=  what a person actually holds
 *
 * Four rules this page obeys, because a role editor is the one screen where a
 * cheerful default is a security bug:
 *   * **Additive only.** A custom role adds to the built-in role and never takes
 *     away, so the UI says "adds to <role>" instead of implying a replacement;
 *   * **the server decides, the UI explains.** The catalogue carries
 *     `grantable` — the subset this caller may bundle. A permission outside it
 *     is still *shown*, disabled, with the reason on the label: an option that
 *     silently disappears teaches nobody why, and a refusal (403
 *     `escalation_refused`) is displayed verbatim when one still happens;
 *   * **nothing is assumed about an empty list.** A failed load shows the error
 *     and the retry button — an empty module list and a broken request are
 *     different things and must not look the same;
 *   * **deactivation is offered before deletion**, since a role in use has
 *     assignments that deleting would silently drop.
 */

interface PermissionModule {
  module: string;
  label: string;
  permissions: string[];
  grantable: string[];
}

interface CatalogueResponse {
  modules: PermissionModule[];
  total_permissions: number;
  ungrouped: string[];
}

interface CustomRole {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  modules: Record<string, number>;
  is_active: boolean;
  updated_at: string | null;
}

interface RoleListResponse {
  roles: CustomRole[];
  subject_types: string[];
  max_roles: number;
}

interface Assignment {
  id: string;
  subject_type: string;
  subject_id: string;
  created_at: string | null;
}

interface AssignmentListResponse {
  role_id: string;
  assignments: Assignment[];
}

interface EffectivePermissions {
  user_id: string;
  email: string;
  builtin_role: string;
  builtin_permissions: string[];
  custom_roles: Array<{ id?: string; name: string; permissions?: string[] }>;
  custom_permissions: string[];
  effective_permissions: string[];
}

const SUBJECT_LABEL: Record<string, string> = {
  user: "Person",
  service_account: "Service account",
  scim_group: "SCIM group",
};

export default function CustomRolesPage() {
  const [catalogue, setCatalogue] = useState<CatalogueResponse | null>(null);
  const [roles, setRoles] = useState<RoleListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftPermissions, setDraftPermissions] = useState<string[]>([]);
  const [assignments, setAssignments] = useState<AssignmentListResponse | null>(null);
  const [subjectType, setSubjectType] = useState("user");
  const [subjectId, setSubjectId] = useState("");
  const [previewUserId, setPreviewUserId] = useState("");
  const [preview, setPreview] = useState<EffectivePermissions | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [catalogueRows, roleRows] = await Promise.all([
        request<CatalogueResponse>("/api/auth/permission-catalogue"),
        request<RoleListResponse>("/api/auth/custom-roles"),
      ]);
      setCatalogue(catalogueRows);
      setRoles(roleRows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load roles");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selected = useMemo(
    () => roles?.roles.find((role) => role.id === selectedId) ?? null,
    [roles, selectedId],
  );

  const loadAssignments = useCallback(async (roleId: string) => {
    try {
      setAssignments(await request<AssignmentListResponse>(`/api/auth/custom-roles/${roleId}/assignments`));
    } catch (err) {
      setAssignments({ role_id: roleId, assignments: [] });
      setError(err instanceof ApiError ? err.message : "Failed to load assignments");
    }
  }, []);

  const selectRole = useCallback(
    (role: CustomRole) => {
      setSelectedId(role.id);
      setDraftName(role.name);
      setDraftDescription(role.description);
      setDraftPermissions(role.permissions);
      setNotice(null);
      void loadAssignments(role.id);
    },
    [loadAssignments],
  );

  const startNew = () => {
    setSelectedId(null);
    setDraftName("");
    setDraftDescription("");
    setDraftPermissions([]);
    setAssignments(null);
    setNotice("New role — pick the permissions it adds, then save.");
  };

  const toggle = (permission: string) => {
    setDraftPermissions((current) =>
      current.includes(permission)
        ? current.filter((item) => item !== permission)
        : [...current, permission].sort(),
    );
  };

  const save = async () => {
    setError(null);
    setNotice(null);
    if (!draftName.trim()) {
      setError("A role needs a name.");
      return;
    }
    try {
      if (selectedId) {
        const updated = await request<CustomRole>(`/api/auth/custom-roles/${selectedId}`, {
          method: "PATCH",
          body: JSON.stringify({
            name: draftName,
            description: draftDescription,
            permissions: draftPermissions,
          }),
        });
        setNotice(`Saved “${updated.name}”. Grants take effect within 30 seconds.`);
      } else {
        const created = await request<CustomRole>("/api/auth/custom-roles", {
          method: "POST",
          body: JSON.stringify({
            name: draftName,
            description: draftDescription,
            permissions: draftPermissions,
          }),
        });
        setNotice(`Created “${created.name}”. Assign it to somebody below.`);
        setSelectedId(created.id);
        void loadAssignments(created.id);
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed");
    }
  };

  const setActive = async (role: CustomRole, active: boolean) => {
    setError(null);
    try {
      await request<CustomRole>(`/api/auth/custom-roles/${role.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: active }),
      });
      setNotice(
        active
          ? `“${role.name}” is active again.`
          : `“${role.name}” is inactive — its assignments grant nothing until you reactivate it.`,
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Update failed");
    }
  };

  const removeRole = async (role: CustomRole) => {
    setError(null);
    try {
      await request<void>(`/api/auth/custom-roles/${role.id}`, { method: "DELETE" });
      setNotice(`Deleted “${role.name}”.`);
      setSelectedId(null);
      setAssignments(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed");
    }
  };

  const assign = async () => {
    setError(null);
    setNotice(null);
    if (!selectedId || !subjectId.trim()) {
      setError("Pick a role and paste a subject id.");
      return;
    }
    try {
      await request<Assignment>(`/api/auth/custom-roles/${selectedId}/assignments`, {
        method: "POST",
        body: JSON.stringify({ subject_type: subjectType, subject_id: subjectId.trim() }),
      });
      setNotice("Assigned. Repeating the same grant is a no-op, not a duplicate.");
      setSubjectId("");
      await loadAssignments(selectedId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Assignment failed");
    }
  };

  const unassign = async (assignment: Assignment) => {
    setError(null);
    try {
      await request<void>(
        `/api/auth/custom-roles/${selectedId}/assignments/${assignment.id}`,
        { method: "DELETE" },
      );
      if (selectedId) await loadAssignments(selectedId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove the assignment");
    }
  };

  const showPreview = async () => {
    setPreviewError(null);
    setPreview(null);
    if (!previewUserId.trim()) {
      setPreviewError("Paste the user id to preview.");
      return;
    }
    try {
      setPreview(
        await request<EffectivePermissions>(
          `/api/auth/effective-permissions?user_id=${encodeURIComponent(previewUserId.trim())}`,
        ),
      );
    } catch (err) {
      setPreviewError(err instanceof ApiError ? err.message : "Preview failed");
    }
  };

  if (error && !catalogue && !roles) {
    return (
      <div className="error-banner" data-testid="roles-load-error">
        {error}
        <button type="button" onClick={() => void load()}>
          Try again
        </button>
      </div>
    );
  }
  if (!catalogue || !roles) return <div className="loading">Loading…</div>;

  return (
    <>
      <header className="page-head">
        <h1>Custom roles</h1>
        <p className="muted">
          A custom role <strong>adds</strong> permissions to a person&apos;s built-in role. It never
          removes one — for less access, choose a smaller built-in role.
        </p>
      </header>

      {error && (
        <div className="error-banner" data-testid="roles-error">
          {error}
        </div>
      )}
      {notice && <div className="notice-banner">{notice}</div>}

      <section className="card">
        <div className="row-between">
          <h2>Roles ({roles.roles.length} of {roles.max_roles})</h2>
          <button type="button" onClick={startNew}>
            New role
          </button>
        </div>
        {roles.roles.length === 0 ? (
          <p className="muted">
            No custom roles yet. The built-in roles (owner, admin, manager, agent, viewer)
            already cover most teams.
          </p>
        ) : (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Data table">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Permissions</th>
                  <th>Modules</th>
                  <th>Active</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {roles.roles.map((role) => (
                  <tr key={role.id}>
                    <td>
                      <button type="button" className="link" onClick={() => selectRole(role)}>
                        {role.name}
                      </button>
                      {role.description && <div className="muted small">{role.description}</div>}
                    </td>
                    <td>{role.permissions.length}</td>
                    <td className="muted small">{Object.keys(role.modules).sort().join(", ") || "—"}</td>
                    <td>{role.is_active ? "yes" : "no"}</td>
                    <td>
                      <button type="button" onClick={() => void setActive(role, !role.is_active)}>
                        {role.is_active ? "Deactivate" : "Activate"}
                      </button>{" "}
                      <button type="button" onClick={() => void removeRole(role)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>{selected ? `Edit “${selected.name}”` : "New role"}</h2>
        <label>
          Name
          <input value={draftName} onChange={(event) => setDraftName(event.target.value)} />
        </label>
        <label>
          Description
          <input
            value={draftDescription}
            onChange={(event) => setDraftDescription(event.target.value)}
            placeholder="What this role is for, in one line"
          />
        </label>
        <p className="muted small">
          {draftPermissions.length} of {catalogue.total_permissions} permission(s) selected.
        </p>
        {catalogue.modules.map((module) => (
          <fieldset key={module.module}>
            <legend>
              {module.label || module.module} ({module.permissions.length})
            </legend>
            {module.permissions.map((permission) => {
              const grantable = module.grantable.includes(permission);
              return (
                <label
                  key={permission}
                  className="inline"
                  title={grantable ? undefined : "You do not hold this permission, so you cannot grant it"}
                >
                  <input
                    type="checkbox"
                    disabled={!grantable}
                    checked={draftPermissions.includes(permission)}
                    onChange={() => toggle(permission)}
                  />
                  <code>{permission}</code>
                  {!grantable && <span className="muted small"> — not yours to grant</span>}
                </label>
              );
            })}
          </fieldset>
        ))}
        <button type="button" onClick={() => void save()}>
          {selected ? "Save changes" : "Create role"}
        </button>
      </section>

      {selected && (
        <section className="card">
          <h2>Assignments</h2>
          <p className="muted small">
            A role can be granted to a person, a service account or a SCIM group. Suspending a
            membership still leaves reads; it never leaves a write.
          </p>
          <div className="row">
            <select value={subjectType} onChange={(event) => setSubjectType(event.target.value)}>
              {roles.subject_types.map((kind) => (
                <option key={kind} value={kind}>
                  {SUBJECT_LABEL[kind] ?? kind}
                </option>
              ))}
            </select>
            <input
              value={subjectId}
              onChange={(event) => setSubjectId(event.target.value)}
              placeholder="subject uuid"
            />
            <button type="button" onClick={() => void assign()}>
              Assign
            </button>
          </div>
          {assignments === null ? (
            <div className="loading">Loading assignments…</div>
          ) : assignments.assignments.length === 0 ? (
            <p className="muted">Nobody holds this role yet.</p>
          ) : (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Data table">
              <table>
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Subject</th>
                    <th>Since</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {assignments.assignments.map((assignment) => (
                    <tr key={assignment.id}>
                      <td>{SUBJECT_LABEL[assignment.subject_type] ?? assignment.subject_type}</td>
                      <td>
                        <code>{assignment.subject_id}</code>
                      </td>
                      <td>{assignment.created_at ?? "—"}</td>
                      <td>
                        <button type="button" onClick={() => void unassign(assignment)}>
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <section className="card">
        <h2>Effective permissions</h2>
        <p className="muted small">
          What a person actually holds: their built-in role plus every active custom role granted to
          them, directly or through a SCIM group.
        </p>
        <div className="row">
          <input
            value={previewUserId}
            onChange={(event) => setPreviewUserId(event.target.value)}
            placeholder="user uuid"
          />
          <button type="button" onClick={() => void showPreview()}>
            Preview
          </button>
        </div>
        {previewError && <div className="error-banner">{previewError}</div>}
        {preview && (
          <div>
            <p>
              Built-in role <strong>{preview.builtin_role}</strong> ·{" "}
              {preview.builtin_permissions.length} permission(s) · custom adds{" "}
              {preview.custom_permissions.length} · total {preview.effective_permissions.length}
            </p>
            {preview.custom_roles.length > 0 && (
              <p className="muted small">
                Custom roles: {preview.custom_roles.map((role) => role.name).join(", ")}
              </p>
            )}
            <details>
              <summary>All {preview.effective_permissions.length} permissions</summary>
              <ul className="small">
                {preview.effective_permissions.map((permission) => (
                  <li key={permission}>
                    <code>{permission}</code>
                  </li>
                ))}
              </ul>
            </details>
          </div>
        )}
      </section>
    </>
  );
}
