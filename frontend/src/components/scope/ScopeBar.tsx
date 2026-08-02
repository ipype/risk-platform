/**
 * What is currently being looked at, and whether it can be written to.
 *
 * The breadcrumb is the keyboard-reachable half of the sidebar: every row action lives
 * here too, so the tree can stay a single tab stop.
 *
 * The rollup notice is not decoration. Selecting a program means reads cover every project
 * under it while writes are refused, and an analyst who types a risk into a program and
 * gets a 422 back deserves to have been told first.
 */

import { useScope } from "../../ScopeContext";
import { kindLabel, legalChildKinds, subtreeIds } from "../../scope-types";
import type { ScopeNode } from "../../scope-types";

export interface ScopeBarProps {
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  onCreate: (parent: ScopeNode | null) => void;
  onEdit: (node: ScopeNode) => void;
}

export function ScopeBar({ sidebarOpen, onToggleSidebar, onCreate, onEdit }: ScopeBarProps) {
  const { nodes, path, active, canAuthor, scopeId, select } = useScope();

  const covered = scopeId === null ? 0 : subtreeIds(nodes, scopeId).length;
  const canCreateUnder = active !== null && legalChildKinds(active).length > 0;

  return (
    <div className="scopebar">
      <button
        className="btn small scopebar-toggle"
        onClick={onToggleSidebar}
        aria-expanded={sidebarOpen}
      >
        Scopes
      </button>

      <nav className="scopecrumb" aria-label="Scope">
        {path.length === 0 ? (
          <span className="muted">No scope selected</span>
        ) : (
          path.map((node, i) => (
            <span key={node.id} className="scopecrumb-part">
              {i > 0 ? <span className="scopecrumb-sep">{"\u203a"}</span> : null}
              <button
                className={i === path.length - 1 ? "scopecrumb-link current" : "scopecrumb-link"}
                onClick={() => select(node.id)}
                aria-current={i === path.length - 1 ? "true" : undefined}
              >
                {node.name}
              </button>
            </span>
          ))
        )}
        {active ? (
          <span className={`scopechip kind-${active.kind}`}>{kindLabel(active.kind)}</span>
        ) : null}
      </nav>

      <div className="scopebar-spacer" />

      {!canAuthor && active ? (
        <span className="scopebar-notice">
          Rollup of {covered} scope{covered === 1 ? "" : "s"} — reads only. Select a project to
          add or change work.
        </span>
      ) : null}

      <button
        className="btn small"
        onClick={() => onCreate(canCreateUnder ? active : null)}
        title={canCreateUnder ? `Add under ${active?.name}` : "Add a top-level scope"}
      >
        New…
      </button>
      <button className="btn small" onClick={() => active && onEdit(active)} disabled={!active}>
        Edit
      </button>
    </div>
  );
}
