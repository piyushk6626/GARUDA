import { useCallback, useEffect, useRef, useState } from "react";
import type L from "leaflet";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { fetchScenario } from "./api";
import { ChatPanel } from "./components/ChatPanel";
import { DataPanel } from "./components/DataPanel";
import { MapView } from "./components/MapView";
import { COUNT_KEYS, COUNT_LABEL } from "./format";
import type { Scenario } from "./types";

type Theme = "light" | "dark";

function storedTheme(): Theme {
  const stored = localStorage.getItem("garuda-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function useTheme() {
  const [theme, setTheme] = useState<Theme>(storedTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  return { theme, setTheme };
}

export function App() {
  const { theme, setTheme } = useTheme();
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const frame = useRef(0);
  const onResize = useCallback(() => {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => mapRef.current?.invalidateSize());
  }, []);

  useEffect(() => {
    fetchScenario().then(setScenario).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Could not load the scenario.");
    });
  }, []);

  return (
    <>
      <header>
        <div className="brand">
          <span className="mark">G</span>
          <div>
            <strong>GARUDA</strong>
            <em>Mission picture</em>
          </div>
        </div>
        {scenario ? <span className="seed">Seed {scenario.seed}</span> : <span className="seed">Loading</span>}
        <button
          type="button"
          className="theme-toggle"
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          onClick={() => {
            setTheme((current) => {
              const next = current === "dark" ? "light" : "dark";
              localStorage.setItem("garuda-theme", next);
              return next;
            });
          }}
        >
          {theme === "dark" ? "Light" : "Dark"}
        </button>
        <div className="metrics">
          {scenario
            ? COUNT_KEYS.map((key) => (
                <span className="metric" key={key}>
                  {COUNT_LABEL[key] || key} <b>{Number(scenario.counts[key] || 0).toLocaleString("en-US")}</b>
                </span>
              ))
            : null}
        </div>
      </header>
      {error ? <p className="banner">{error}</p> : null}
      <main className="workspace">
        <PanelGroup direction="horizontal" autoSaveId="garuda-layout" className="workspace-group">
          <Panel defaultSize={28} minSize={16} maxSize={48} onResize={onResize}>
            <DataPanel scenario={scenario} />
          </Panel>
          <PanelResizeHandle className="resize-handle" />
          <Panel minSize={24} onResize={onResize}>
            <MapView scenario={scenario} mapRef={mapRef} theme={theme} />
          </Panel>
          <PanelResizeHandle className="resize-handle" />
          <Panel defaultSize={26} minSize={18} maxSize={46} onResize={onResize}>
            <ChatPanel />
          </Panel>
        </PanelGroup>
      </main>
    </>
  );
}
