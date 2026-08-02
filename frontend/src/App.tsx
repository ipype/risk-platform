import { useState } from "react";
import RegisterView from "./views/RegisterView";
import MatrixView from "./views/MatrixView";
import MatrixSettings from "./views/MatrixSettings";
import CustomFieldsView from "./views/CustomFieldsView";
import ActivityView from "./views/ActivityView";
import MappingView from "./views/MappingView";
import ScheduleView from "./views/ScheduleView";
import GanttView from "./views/GanttView";
import QuantifyView from "./views/QuantifyView";
import SimulationView from "./views/SimulationView";
import { ScopeProvider, useScope } from "./ScopeContext";
import { ScopeBar } from "./components/scope/ScopeBar";
import { ScopeEditPanel } from "./components/scope/ScopeEditPanel";
import type { ScopePanelMode } from "./components/scope/ScopeEditPanel";
import { ScopeTree } from "./components/scope/ScopeTree";
import type { ScopeNode } from "./scope-types";
import { getActor, setActor } from "./api";
import "./scope.css";

type View =
  | "register"
  | "matrix"
  | "quantify"
  | "simulate"
  | "schedule"
  | "gantt"
  | "mapping"
  | "activity"
  | "fields"
  | "settings";

export default function App() {
  return (
    <ScopeProvider>
      <Shell />
    </ScopeProvider>
  );
}

/**
 * Nothing renders until the hierarchy has loaded.
 *
 * Every view fetches on mount and every fetch now carries a scope, so rendering the
 * register before the tree settles would fire one unscoped round of requests and a second
 * scoped one a moment later — and briefly show another project's rows while doing it.
 */
function Shell() {
  const scope = useScope();
  const [view, setView] = useState<View>("register");
  const [actor, setActorState] = useState<string>(getActor());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [panel, setPanel] = useState<ScopePanelMode | null>(null);

  function onActorChange(v: string) {
    setActorState(v);
    setActor(v || "Unknown");
  }

  function selectScope(id: number) {
    scope.select(id);
    setSidebarOpen(false);
  }

  function handleSaved(node: ScopeNode | null) {
    const wasCreate = panel?.kind === "create";
    setPanel(null);
    // Selection first: `reload` resolves against whatever the module store now holds, so
    // a node created here is the one the refreshed tree opens on.
    if (wasCreate && node !== null) scope.select(node.id);
    void scope.reload();
  }

  if (scope.loading && scope.nodes.length === 0) {
    return <div className="scope-boot">Loading scopes…</div>;
  }

  if (scope.error !== null && scope.nodes.length === 0) {
    return (
      <div className="scope-boot">
        <div className="error">{scope.error}</div>
        <p>The platform needs the scope hierarchy before it can show anything scoped to it.</p>
        <button className="btn primary" onClick={() => void scope.reload()}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="app-shell">
      {sidebarOpen ? (
        <div className="scopeside-scrim" onClick={() => setSidebarOpen(false)} />
      ) : null}

      <aside className={sidebarOpen ? "scopeside open" : "scopeside"}>
        <div className="scopeside-head">
          <h2>Scopes</h2>
          <div className="spacer" />
          <button
            className="btn small"
            onClick={() => setPanel({ kind: "create", parent: null })}
            title="Add a top-level scope"
          >
            New
          </button>
          <button className="link scopeside-close" onClick={() => setSidebarOpen(false)}>
            Close
          </button>
        </div>
        <div className="scopeside-body">
          {scope.error !== null ? <div className="error scopeside-error">{scope.error}</div> : null}
          <ScopeTree
            tree={scope.tree}
            scopeId={scope.scopeId}
            onSelect={selectScope}
            onCreate={(parent) => setPanel({ kind: "create", parent })}
            onEdit={(node) => setPanel({ kind: "edit", node })}
          />
        </div>
      </aside>

      <div className="app">
        <ScopeBar
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen((open) => !open)}
          onCreate={(parent) => setPanel({ kind: "create", parent })}
          onEdit={(node) => setPanel({ kind: "edit", node })}
        />

        <nav className="mainnav">
          <button className={view === "register" ? "navlink active" : "navlink"} onClick={() => setView("register")}>
            Register
          </button>
          <button className={view === "matrix" ? "navlink active" : "navlink"} onClick={() => setView("matrix")}>
            Matrix
          </button>
          <button className={view === "quantify" ? "navlink active" : "navlink"} onClick={() => setView("quantify")}>
            Quantify
          </button>
          <button className={view === "simulate" ? "navlink active" : "navlink"} onClick={() => setView("simulate")}>
            Simulate
          </button>
          <button className={view === "schedule" ? "navlink active" : "navlink"} onClick={() => setView("schedule")}>
            Schedule
          </button>
          <button className={view === "gantt" ? "navlink active" : "navlink"} onClick={() => setView("gantt")}>
            Gantt
          </button>
          <button className={view === "mapping" ? "navlink active" : "navlink"} onClick={() => setView("mapping")}>
            Schedule mapping
          </button>
          <button className={view === "activity" ? "navlink active" : "navlink"} onClick={() => setView("activity")}>
            Activity
          </button>
          <button className={view === "fields" ? "navlink active" : "navlink"} onClick={() => setView("fields")}>
            Fields
          </button>
          <button className={view === "settings" ? "navlink active" : "navlink"} onClick={() => setView("settings")}>
            Matrix settings
          </button>
          <div className="nav-spacer" />
          <label className="identity">
            You:
            <input
              value={actor}
              onChange={(e) => onActorChange(e.target.value)}
              placeholder="your name"
            />
          </label>
        </nav>

        {/*
          Keyed on the scope so switching project remounts every view rather than leaving
          a schedule version, a selected run or an open filter from another project on
          screen. The tab itself survives, because the question being asked has not changed
          — only which project is being asked about.
        */}
        <div className="viewhost" key={scope.scopeId ?? "unscoped"}>
          {view === "register" && <RegisterView />}
          {view === "matrix" && <MatrixView />}
          {view === "quantify" && <QuantifyView />}
          {view === "simulate" && <SimulationView />}
          {view === "schedule" && <ScheduleView />}
          {view === "gantt" && <GanttView />}
          {view === "mapping" && <MappingView />}
          {view === "activity" && <ActivityView />}
          {view === "fields" && <CustomFieldsView />}
          {view === "settings" && <MatrixSettings />}
        </div>
      </div>

      <ScopeEditPanel
        mode={panel}
        nodes={scope.nodes}
        onClose={() => setPanel(null)}
        onSaved={handleSaved}
      />
    </div>
  );
}
