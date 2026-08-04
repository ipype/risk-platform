/**
 * The hierarchy as a sidebar.
 *
 * Selecting a node is expensive — every view remounts and refetches against it — so
 * selection is explicit: click, Enter or Space. Arrow keys move focus and open and close
 * branches without changing what the rest of the app is looking at, which is the standard
 * tree behaviour and also the right one here.
 *
 * The row's own buttons carry `tabIndex={-1}`. A tree is one tab stop; putting three
 * stops on every row of a hundred-project portfolio makes the sidebar unusable by
 * keyboard. Everything they do is reachable from the scope bar, which is in the tab order.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { kindLabel, legalChildKinds } from "../../scope-types";
import type { ScopeNode, ScopeTreeNode } from "../../scope-types";

const KIND_CHIP: Record<string, string> = {
  portfolio: "Pf",
  program: "Pg",
  project: "Pj",
};

interface VisibleRow {
  node: ScopeNode;
  depth: number;
  hasChildren: boolean;
  parentId: number | null;
}

function flatten(
  tree: ScopeTreeNode[],
  collapsed: Set<number>,
  out: VisibleRow[] = [],
  parentId: number | null = null
): VisibleRow[] {
  for (const entry of tree) {
    const hasChildren = entry.children.length > 0;
    out.push({ node: entry.node, depth: entry.depth, hasChildren, parentId });
    if (hasChildren && !collapsed.has(entry.node.id)) {
      flatten(entry.children, collapsed, out, entry.node.id);
    }
  }
  return out;
}

export interface ScopeTreeProps {
  tree: ScopeTreeNode[];
  scopeId: number | null;
  onSelect: (id: number) => void;
  onCreate: (parent: ScopeNode | null) => void;
  onEdit: (node: ScopeNode) => void;
}

export function ScopeTree({ tree, scopeId, onSelect, onCreate, onEdit }: ScopeTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<number>>(() => new Set());
  const [focusId, setFocusId] = useState<number | null>(null);
  const rowRefs = useRef(new Map<number, HTMLLIElement>());

  const visible = useMemo(() => flatten(tree, collapsed), [tree, collapsed]);

  // The roving tab stop: whatever was last focused, else the selection, else the top row.
  const tabStop =
    (focusId !== null && visible.some((r) => r.node.id === focusId) && focusId) ||
    (scopeId !== null && visible.some((r) => r.node.id === scopeId) && scopeId) ||
    visible[0]?.node.id ||
    null;

  const focusRow = useCallback((id: number) => {
    setFocusId(id);
    rowRefs.current.get(id)?.focus();
  }, []);

  const toggle = useCallback((id: number, open?: boolean) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      const shouldOpen = open ?? next.has(id);
      if (shouldOpen) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  function onKeyDown(event: React.KeyboardEvent<HTMLUListElement>) {
    if (tabStop === null) return;
    const index = visible.findIndex((r) => r.node.id === tabStop);
    if (index < 0) return;
    const row = visible[index];

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (index < visible.length - 1) focusRow(visible[index + 1].node.id);
        break;
      case "ArrowUp":
        event.preventDefault();
        if (index > 0) focusRow(visible[index - 1].node.id);
        break;
      case "ArrowRight":
        event.preventDefault();
        if (row.hasChildren && collapsed.has(row.node.id)) toggle(row.node.id, true);
        else if (row.hasChildren && index < visible.length - 1)
          focusRow(visible[index + 1].node.id);
        break;
      case "ArrowLeft":
        event.preventDefault();
        if (row.hasChildren && !collapsed.has(row.node.id)) toggle(row.node.id, false);
        else if (row.parentId !== null) focusRow(row.parentId);
        break;
      case "Home":
        event.preventDefault();
        focusRow(visible[0].node.id);
        break;
      case "End":
        event.preventDefault();
        focusRow(visible[visible.length - 1].node.id);
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        onSelect(row.node.id);
        break;
      default:
        break;
    }
  }

  if (visible.length === 0) {
    return (
      <div className="scopetree-empty">
        <p>No scopes yet.</p>
        <button className="btn small" onClick={() => onCreate(null)}>
          New scope
        </button>
      </div>
    );
  }

  function renderBranch(branch: ScopeTreeNode[]) {
    return branch.map((entry) => {
      const { node, children } = entry;
      const hasChildren = children.length > 0;
      const isOpen = hasChildren && !collapsed.has(node.id);
      const selected = node.id === scopeId;

      return (
        <li
          key={node.id}
          role="treeitem"
          aria-level={entry.depth + 1}
          aria-selected={selected}
          aria-expanded={hasChildren ? isOpen : undefined}
          tabIndex={node.id === tabStop ? 0 : -1}
          className={selected ? "scopeitem selected" : "scopeitem"}
          ref={(el) => {
            if (el) rowRefs.current.set(node.id, el);
            else rowRefs.current.delete(node.id);
          }}
          onFocus={(e) => {
            if (e.target === e.currentTarget) setFocusId(node.id);
          }}
          onClick={(e) => {
            e.stopPropagation();
            onSelect(node.id);
          }}
        >
          <div className="scoperow" style={{ paddingLeft: 6 + entry.depth * 14 }}>
            {hasChildren ? (
              <button
                type="button"
                className="scopetwist"
                tabIndex={-1}
                aria-label={isOpen ? `Collapse ${node.name}` : `Expand ${node.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  toggle(node.id);
                }}
              >
                {isOpen ? "\u25be" : "\u25b8"}
              </button>
            ) : (
              <span className="scopetwist blank" aria-hidden="true" />
            )}

            <span className={`scopechip kind-${node.kind}`} title={kindLabel(node.kind)}>
              {KIND_CHIP[node.kind] ?? "?"}
            </span>

            <span className="scopename" title={node.name}>
              {node.name}
            </span>

            {/* Codes cost ~30px per row and only earn that space on the row that's
                already claiming attention — everywhere else they're the difference
                between "Southern Loop 1/2/3" being distinguishable and all three
                reading "South…" (design handoff, 2026-08-02). */}
            {node.code && selected ? <span className="scopecode">{node.code}</span> : null}
            {node.is_default ? (
              <span className="scopedefault" title="Default project — unscoped work lands here">
                {"\u2605"}
              </span>
            ) : null}
            {node.risk_count_subtree > 0 ? (
              <span
                className="scopecount"
                title={`${node.risk_count_subtree} risk(s) in this scope and below`}
              >
                {node.risk_count_subtree}
              </span>
            ) : null}

            <span className="scopeactions">
              {legalChildKinds(node).length > 0 ? (
                <button
                  type="button"
                  className="scopeaction"
                  tabIndex={-1}
                  title={`Add under ${node.name}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onCreate(node);
                  }}
                >
                  +
                </button>
              ) : null}
              <button
                type="button"
                className="scopeaction"
                tabIndex={-1}
                title={`Edit ${node.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onEdit(node);
                }}
              >
                {"\u22ef"}
              </button>
            </span>
          </div>

          {isOpen ? (
            <ul role="group" className="scopebranch">
              {renderBranch(children)}
            </ul>
          ) : null}
        </li>
      );
    });
  }

  return (
    <ul role="tree" aria-label="Scopes" className="scopetree" onKeyDown={onKeyDown}>
      {renderBranch(tree)}
    </ul>
  );
}
