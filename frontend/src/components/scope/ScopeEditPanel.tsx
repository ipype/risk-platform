/**
 * Create, rename, move, default and delete, in the slide-over the register already uses.
 *
 * Kind is fixed once a node exists. `ScopeUpdate` has no `kind` field and that is correct:
 * turning a program into a project would orphan its children under a node that cannot
 * hold them, and the honest version of that operation is a new node and a move.
 *
 * Delete refusals are printed as the server sent them, one line per blocker. "Cannot be
 * deleted" is not useful; "3 risks belong to it, 1 simulation run belongs to it" is a list
 * of things to go and do.
 */

import { useEffect, useState } from "react";
import { ScopeApiError, createScope, deleteScope, setDefaultScope, updateScope } from "../../scope-api";
import { isOwning, kindLabel, legalChildKinds, legalParents, scopePath } from "../../scope-types";
import type { ScopeKind, ScopeNode } from "../../scope-types";

export type ScopePanelMode =
  | { kind: "create"; parent: ScopeNode | null }
  | { kind: "edit"; node: ScopeNode };

export interface ScopeEditPanelProps {
  mode: ScopePanelMode | null;
  nodes: ScopeNode[];
  onClose: () => void;
  /** `null` means the node was deleted. */
  onSaved: (node: ScopeNode | null) => void;
}

const ROOT_VALUE = "root";

export function ScopeEditPanel({ mode, nodes, onClose, onSaved }: ScopeEditPanelProps) {
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  const [kind, setKind] = useState<ScopeKind>("project");
  const [parentValue, setParentValue] = useState<string>(ROOT_VALUE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    setError(null);
    setReasons([]);
    setConfirmingDelete(false);
    setBusy(false);
    if (mode === null) return;
    if (mode.kind === "create") {
      const allowed = legalChildKinds(mode.parent);
      setName("");
      setCode("");
      setDescription("");
      setKind(allowed[allowed.length - 1] ?? "project");
      setParentValue(mode.parent === null ? ROOT_VALUE : String(mode.parent.id));
    } else {
      setName(mode.node.name);
      setCode(mode.node.code ?? "");
      setDescription(mode.node.description ?? "");
      setParentValue(mode.node.parent_id === null ? ROOT_VALUE : String(mode.node.parent_id));
    }
  }, [mode]);

  function report(e: unknown) {
    if (e instanceof ScopeApiError) {
      setError(e.message);
      setReasons(e.reasons);
    } else {
      setError(e instanceof Error ? e.message : String(e));
      setReasons([]);
    }
  }

  const open = mode !== null;
  const editing = mode?.kind === "edit" ? mode.node : null;
  const creatingUnder = mode?.kind === "create" ? mode.parent : null;
  const allowedKinds = mode?.kind === "create" ? legalChildKinds(creatingUnder) : [];
  const parentOptions = editing ? legalParents(nodes, editing) : [];
  const trimmedName = name.trim();

  async function save() {
    if (mode === null || trimmedName === "") return;
    setBusy(true);
    setError(null);
    setReasons([]);
    try {
      if (mode.kind === "create") {
        const created = await createScope({
          kind,
          name: trimmedName,
          parent_id: mode.parent?.id ?? null,
          code: code.trim() || null,
          description: description.trim() || null,
        });
        onSaved(created);
      } else {
        const nextParent = parentValue === ROOT_VALUE ? null : Number(parentValue);
        const updated = await updateScope(mode.node.id, {
          name: trimmedName,
          code: code.trim() || null,
          description: description.trim() || null,
          ...(nextParent === mode.node.parent_id ? {} : { parent_id: nextParent }),
        });
        onSaved(updated);
      }
    } catch (e) {
      report(e);
      setBusy(false);
    }
  }

  async function makeDefault() {
    if (editing === null) return;
    setBusy(true);
    setError(null);
    try {
      onSaved(await setDefaultScope(editing.id));
    } catch (e) {
      report(e);
      setBusy(false);
    }
  }

  async function remove() {
    if (editing === null) return;
    setBusy(true);
    setError(null);
    setReasons([]);
    try {
      await deleteScope(editing.id);
      onSaved(null);
    } catch (e) {
      report(e);
      setBusy(false);
      setConfirmingDelete(false);
    }
  }

  const contains = editing
    ? [
        editing.child_count > 0 ? `${editing.child_count} scope(s)` : null,
        editing.risk_count > 0 ? `${editing.risk_count} risk(s)` : null,
        editing.schedule_file_count > 0 ? `${editing.schedule_file_count} schedule file(s)` : null,
        editing.run_count > 0 ? `${editing.run_count} run(s)` : null,
      ].filter((s): s is string => s !== null)
    : [];

  return (
    <>
      <div className={open ? "backdrop show" : "backdrop"} onClick={onClose} />
      <aside
        className={open ? "panel open" : "panel"}
        role="dialog"
        aria-modal="true"
        aria-label={editing ? `Edit ${editing.name}` : "New scope"}
        aria-hidden={!open}
      >
        <div className="panel-head">
          <h2>
            {editing
              ? `Edit ${kindLabel(editing.kind).toLowerCase()}`
              : creatingUnder
                ? `New under ${creatingUnder.name}`
                : "New scope"}
          </h2>
          <button className="link" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="panel-body">
          {error ? (
            <div className="error">
              {error}
              {reasons.length > 0 ? (
                <ul className="scope-reasons">
                  {reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {mode?.kind === "create" ? (
            <label>
              Kind
              <select value={kind} onChange={(e) => setKind(e.target.value as ScopeKind)}>
                {allowedKinds.map((k) => (
                  <option key={k} value={k}>
                    {kindLabel(k)}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label>
              Kind
              <div className="readonly">
                {kindLabel(editing?.kind ?? "")} — fixed once created
              </div>
            </label>
          )}

          <label>
            Name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Northern Corridor Upgrade"
              maxLength={200}
              autoFocus
            />
          </label>

          <label>
            Code <span className="hint">optional, unique across the platform</span>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="NCU-01"
              maxLength={40}
            />
          </label>

          <label>
            Description
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>

          {editing ? (
            <label>
              Sits under
              <select value={parentValue} onChange={(e) => setParentValue(e.target.value)}>
                <option value={ROOT_VALUE}>No parent (top level)</option>
                {parentOptions.map((candidate) => (
                  <option key={candidate.id} value={String(candidate.id)}>
                    {scopePath(nodes, candidate.id)
                      .map((n) => n.name)
                      .join(" › ")}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label>
              Sits under
              <div className="readonly">
                {creatingUnder
                  ? scopePath(nodes, creatingUnder.id)
                      .map((n) => n.name)
                      .join(" › ")
                  : "No parent (top level)"}
              </div>
            </label>
          )}

          {editing ? (
            <div className="scope-meta">
              <div className="scope-contains">
                {contains.length > 0 ? `Contains ${contains.join(" · ")}` : "Contains nothing yet"}
              </div>
              {isOwning(editing) ? (
                editing.is_default ? (
                  <div className="scope-contains">
                    Default project — work created without a scope lands here.
                  </div>
                ) : (
                  <button className="btn small" onClick={makeDefault} disabled={busy}>
                    Make default project
                  </button>
                )
              ) : null}
            </div>
          ) : null}

          {editing ? (
            <div className="scope-danger">
              {confirmingDelete ? (
                <>
                  <span>Delete {editing.name}? Nothing inside it is removed.</span>
                  <button className="btn small" onClick={() => setConfirmingDelete(false)} disabled={busy}>
                    Cancel
                  </button>
                  <button className="link danger" onClick={remove} disabled={busy}>
                    Confirm delete
                  </button>
                </>
              ) : (
                <button className="link danger" onClick={() => setConfirmingDelete(true)} disabled={busy}>
                  Delete this scope
                </button>
              )}
            </div>
          ) : null}
        </div>

        <div className="panel-foot">
          <button className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn primary" onClick={save} disabled={busy || trimmedName === ""}>
            {busy ? "Saving…" : editing ? "Save" : "Create"}
          </button>
        </div>
      </aside>
    </>
  );
}
