import { useState } from "react";
import RegisterView from "./views/RegisterView";
import MatrixView from "./views/MatrixView";
import MatrixSettings from "./views/MatrixSettings";
import CustomFieldsView from "./views/CustomFieldsView";
import ActivityView from "./views/ActivityView";
import { getActor, setActor } from "./api";

type View = "register" | "matrix" | "activity" | "fields" | "settings";

export default function App() {
  const [view, setView] = useState<View>("register");
  const [actor, setActorState] = useState<string>(getActor());

  function onActorChange(v: string) {
    setActorState(v);
    setActor(v || "Unknown");
  }

  return (
    <div className="app">
      <nav className="mainnav">
        <button className={view === "register" ? "navlink active" : "navlink"} onClick={() => setView("register")}>
          Register
        </button>
        <button className={view === "matrix" ? "navlink active" : "navlink"} onClick={() => setView("matrix")}>
          Matrix
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
      {view === "register" && <RegisterView />}
      {view === "matrix" && <MatrixView />}
      {view === "activity" && <ActivityView />}
      {view === "fields" && <CustomFieldsView />}
      {view === "settings" && <MatrixSettings />}
    </div>
  );
}
