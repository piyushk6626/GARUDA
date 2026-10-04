import { useCallback, useEffect, useRef, useState } from "react";
import type L from "leaflet";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { fetchScenario } from "./api";
import { ChatPanel } from "./components/ChatPanel";
import { DataPanel } from "./components/DataPanel";
import { MapView } from "./components/MapView";
import { COUNT_KEYS, COUNT_LABEL } from "./format";
import type { Scenario } from "./types";

export function App() {
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
            <MapView scenario={scenario} mapRef={mapRef} />
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
