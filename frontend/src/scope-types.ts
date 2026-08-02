/**
 * The hierarchy, client side.
 *
 * `GET /scopes` returns every node flat with its `parent_id`, once, because the sidebar
 * wants a tree, the breadcrumb wants a path and the move-parent picker wants a filtered
 * list. Folding one response three ways is cheaper than three endpoints that can disagree,
 * so the folding lives here — pure functions over an array, no fetching, no React.
 */

export type ScopeKind = "portfolio" | "program" | "project";

/** Containment order. A node may only sit under a strictly shallower kind. */
export const SCOPE_KINDS: readonly ScopeKind[] = ["portfolio", "program", "project"] as const;

const SCOPE_RANK: Record<ScopeKind, number> = { portfolio: 0, program: 1, project: 2 };

/** Work is authored on projects. Mirrors `OWNING_KIND` in `app/models/scope.py`. */
export const OWNING_KIND: ScopeKind = "project";

export const KIND_LABEL: Record<ScopeKind, string> = {
  portfolio: "Portfolio",
  program: "Program",
  project: "Project",
};

export interface ScopeNode {
  id: number;
  /** Server-declared, typed loosely on purpose: an unknown kind must render, not crash. */
  kind: string;
  parent_id: number | null;
  name: string;
  code: string | null;
  description: string | null;
  is_default: boolean;
  sort_order: number;
  created_by: string;
  created_at: string;
  /** Rows owned directly. Zero on programs and portfolios by construction. */
  risk_count: number;
  schedule_file_count: number;
  run_count: number;
  child_count: number;
}

export interface ScopeCreate {
  kind: ScopeKind;
  name: string;
  parent_id?: number | null;
  code?: string | null;
  description?: string | null;
}

export interface ScopeUpdate {
  name?: string;
  code?: string | null;
  description?: string | null;
  sort_order?: number;
  /** Present and null moves the node to the root; absent leaves it where it is. */
  parent_id?: number | null;
}

export interface ScopeTreeNode {
  node: ScopeNode;
  children: ScopeTreeNode[];
  depth: number;
}

export function isKind(value: string): value is ScopeKind {
  return (SCOPE_KINDS as readonly string[]).includes(value);
}

export function kindLabel(kind: string): string {
  return isKind(kind) ? KIND_LABEL[kind] : kind;
}

export function isOwning(node: ScopeNode | null | undefined): boolean {
  return node?.kind === OWNING_KIND;
}

export function indexById(nodes: ScopeNode[]): Map<number, ScopeNode> {
  return new Map(nodes.map((n) => [n.id, n]));
}

/**
 * Fold the flat list into a tree, preserving server ordering within each parent.
 *
 * A node whose `parent_id` names something absent is promoted to a root rather than
 * dropped. The server will not produce one, but a filtered or partially failed response
 * must not make rows invisible — an invisible scope is a scope whose register nobody can
 * open.
 */
export function buildScopeTree(nodes: ScopeNode[]): ScopeTreeNode[] {
  const byId = indexById(nodes);
  const wrapped = new Map<number, ScopeTreeNode>(
    nodes.map((node) => [node.id, { node, children: [], depth: 0 }])
  );

  const roots: ScopeTreeNode[] = [];
  for (const node of nodes) {
    const self = wrapped.get(node.id)!;
    const parent =
      node.parent_id !== null && byId.has(node.parent_id)
        ? wrapped.get(node.parent_id)
        : undefined;
    if (parent && parent !== self) parent.children.push(self);
    else roots.push(self);
  }

  // Depth is assigned by walking down from the roots, so a cycle written by hand costs
  // the nodes inside it their place in the sidebar rather than hanging the render.
  const seen = new Set<number>();
  const stack: ScopeTreeNode[] = roots.map((r) => {
    r.depth = 0;
    return r;
  });
  while (stack.length) {
    const current = stack.pop()!;
    if (seen.has(current.node.id)) {
      current.children = [];
      continue;
    }
    seen.add(current.node.id);
    for (const child of current.children) {
      child.depth = current.depth + 1;
      stack.push(child);
    }
  }
  return roots;
}

/** Root-to-node path, for the breadcrumb. Empty when the id is unknown. */
export function scopePath(nodes: ScopeNode[], id: number | null): ScopeNode[] {
  if (id === null) return [];
  const byId = indexById(nodes);
  const path: ScopeNode[] = [];
  const seen = new Set<number>();
  let cursor = byId.get(id);
  while (cursor && !seen.has(cursor.id)) {
    seen.add(cursor.id);
    path.unshift(cursor);
    cursor = cursor.parent_id === null ? undefined : byId.get(cursor.parent_id);
  }
  return path;
}

/** A node and everything beneath it, breadth first. */
export function subtreeIds(nodes: ScopeNode[], id: number): number[] {
  const children = new Map<number | null, number[]>();
  for (const n of nodes) {
    const bucket = children.get(n.parent_id) ?? [];
    bucket.push(n.id);
    children.set(n.parent_id, bucket);
  }
  const out: number[] = [];
  const queue = [id];
  const seen = new Set<number>([id]);
  while (queue.length) {
    const current = queue.shift()!;
    out.push(current);
    for (const child of children.get(current) ?? []) {
      if (!seen.has(child)) {
        seen.add(child);
        queue.push(child);
      }
    }
  }
  return out;
}

/** Kinds that may be created under this parent. `null` parent means the root. */
export function legalChildKinds(parent: ScopeNode | null): ScopeKind[] {
  if (parent === null) return [...SCOPE_KINDS];
  if (!isKind(parent.kind)) return [];
  return SCOPE_KINDS.filter((kind) => SCOPE_RANK[parent.kind as ScopeKind] < SCOPE_RANK[kind]);
}

/**
 * Nodes this one may be moved under: shallower kind, not itself, not its own descendants.
 *
 * The server checks all three and refuses with a named message. Filtering here as well
 * means the refusal is normally never reached, which is the point — an option that cannot
 * work should not be offered.
 */
export function legalParents(nodes: ScopeNode[], node: ScopeNode): ScopeNode[] {
  if (!isKind(node.kind)) return [];
  const forbidden = new Set(subtreeIds(nodes, node.id));
  return nodes.filter(
    (candidate) =>
      !forbidden.has(candidate.id) &&
      isKind(candidate.kind) &&
      SCOPE_RANK[candidate.kind as ScopeKind] < SCOPE_RANK[node.kind as ScopeKind]
  );
}

/**
 * Which node to open on, given what was last selected.
 *
 * Falls through the same order every time: the remembered node if it still exists, then
 * the default project, then the first project, then whatever is first. A remembered id
 * that was deleted elsewhere must not leave the app pointing at nothing.
 */
export function resolveSelection(nodes: ScopeNode[], remembered: number | null): number | null {
  if (nodes.length === 0) return null;
  if (remembered !== null && nodes.some((n) => n.id === remembered)) return remembered;
  const fallback =
    nodes.find((n) => n.is_default && isOwning(n)) ??
    nodes.find((n) => isOwning(n)) ??
    nodes[0];
  return fallback.id;
}
