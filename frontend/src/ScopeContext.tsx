/**
 * The selected scope, for React.
 *
 * One fetch of the whole tree, held here, folded by the pure functions in
 * `scope-types.ts`. Selecting writes through to `scope-state.ts` *before* the state
 * update that re-renders, so every request a remounted view fires already carries the new
 * scope. The two stores exist because the API layer cannot read a context and should not
 * have to; this is the only writer of either.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { listScopes } from "./scope-api";
import { getScopeId, setScopeId } from "./scope-state";
import {
  buildScopeTree,
  indexById,
  isOwning,
  resolveSelection,
  scopePath,
} from "./scope-types";
import type { ScopeNode, ScopeTreeNode } from "./scope-types";

export interface ScopeContextValue {
  nodes: ScopeNode[];
  tree: ScopeTreeNode[];
  byId: Map<number, ScopeNode>;
  scopeId: number | null;
  active: ScopeNode | null;
  /** Root-to-active, for the breadcrumb. */
  path: ScopeNode[];
  /** Whether authored work can land here. False on programs and portfolios. */
  canAuthor: boolean;
  /** True until the first load settles, either way. */
  loading: boolean;
  error: string | null;
  select: (id: number) => void;
  reload: () => Promise<void>;
}

const ScopeContext = createContext<ScopeContextValue | null>(null);

export function ScopeProvider({ children }: { children: ReactNode }) {
  const [nodes, setNodes] = useState<ScopeNode[]>([]);
  const [scopeId, setScopeIdState] = useState<number | null>(getScopeId());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const fetched = await listScopes();
      // Resolved against what is remembered, which may name a node someone else deleted.
      const next = resolveSelection(fetched, getScopeId());
      setScopeId(next);
      setNodes(fetched);
      setScopeIdState(next);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const select = useCallback((id: number) => {
    setScopeId(id);
    setScopeIdState(id);
  }, []);

  const value = useMemo<ScopeContextValue>(() => {
    const byId = indexById(nodes);
    const active = scopeId === null ? null : (byId.get(scopeId) ?? null);
    return {
      nodes,
      tree: buildScopeTree(nodes),
      byId,
      scopeId,
      active,
      path: scopePath(nodes, scopeId),
      canAuthor: isOwning(active),
      loading,
      error,
      select,
      reload,
    };
  }, [nodes, scopeId, loading, error, select, reload]);

  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useScope(): ScopeContextValue {
  const value = useContext(ScopeContext);
  if (value === null) {
    throw new Error("useScope must be used inside a ScopeProvider");
  }
  return value;
}
