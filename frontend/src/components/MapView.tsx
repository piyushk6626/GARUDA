import L from "leaflet";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Circle, CircleMarker, GeoJSON, LayerGroup, LayersControl, MapContainer, Marker, Polyline, Popup, Rectangle, TileLayer, useMap } from "react-leaflet";

import type { IndiaLayer, Row, Scenario } from "../types";

const SATELLITE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const LABELS = "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}";
const STREET_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, &copy; <a href="https://carto.com/attributions">CARTO</a>';
const SAT_ATTR = "Tiles &copy; Esri";

const ZONE_COLOR: Record<string, string> = { CAP: "#2a9d8f", AAR_TRACK: "#8338ec", CORRIDOR: "#3a86ff", TRA: "#f77f00", ADIZ: "#d00000", RESTRICTED: "#9d0208", PROHIBITED: "#6a040f", DANGER: "#e85d04", CTR: "#577590", TMA: "#43aa8b" };
const MISSION_COLOR: Record<string, string> = { DCA: "#1d4e89", CAP: "#0f766e", STRIKE: "#9f1239", SEAD: "#b45309", CAS: "#9a3412", ISR: "#6d28d9", AAR: "#7c3aed", AIRLIFT: "#0369a1", SAR: "#157f3d" };
const THREAT_COLOR: Record<string, string> = { ACTIVE: "#d00000", SUPPRESSED: "#f4a261", DESTROYED: "#888", UNKNOWN: "#9d4edd" };
const THREAT_SHORT: Record<string, string> = { SAM_LONG: "SAM-L", SAM_MED: "SAM-M", SAM_SHORT: "SAM-S", RADAR_EW: "EW", RADAR_GCI: "GCI", AAA: "AAA", ENEMY_CAP: "CAP", AIRFIELD: "AF" };

const baseIcon = L.divIcon({ className: "pin base", iconSize: [13, 13], iconAnchor: [6, 6], html: "<i></i>" });
const targetIcon = L.divIcon({ className: "pin target", iconSize: [13, 13], iconAnchor: [6, 6], html: "<i></i>" });

type Stamp = (layer: L.Layer, text: string | undefined, minZoom: number) => void;
const LabelCtx = createContext<Stamp>(() => {});

type Labeled = { layer: L.Layer; minZoom: number };

export function MapView({ scenario, mapRef }: { scenario: Scenario | null; mapRef: RefObject<L.Map | null> }) {
  const labeled = useRef<Labeled[]>([]);
  const stamp = useMemo<Stamp>(() => (layer, text, minZoom) => {
    if (!text) return;
    layer.bindTooltip(text, { className: "tac", direction: "right", offset: [8, 0], opacity: 1, permanent: true });
    labeled.current.push({ layer, minZoom });
  }, []);
  const [base, setBase] = useState<"street" | "satellite">("street");
  const [labels, setLabels] = useState(true);
  const cartoKey = import.meta.env.VITE_CARTO_API_KEY ?? "";
  const street = `https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png${cartoKey ? `?key=${encodeURIComponent(cartoKey)}` : ""}`;

  return (
    <div className="map-stage">
      <MapContainer center={[26, 80]} zoom={5} zoomSnap={0.25} style={{ height: "100%", width: "100%" }}>
        <MapBinder mapRef={mapRef} />
        <LabelCtx.Provider value={stamp}>
          <LabelRefresh items={labeled} />
          {scenario ? <FitFrame geometry={scenario.india} /> : null}
          <MapChrome />
          {base === "street" ? (
            <TileLayer url={street} maxZoom={20} attribution={STREET_ATTR} />
          ) : (
            <TileLayer url={SATELLITE} maxZoom={19} attribution={SAT_ATTR} />
          )}
          {base === "satellite" && labels ? <TileLayer url={LABELS} maxZoom={19} attribution={SAT_ATTR} /> : null}
          <MapLayers scenario={scenario} />
        </LabelCtx.Provider>
      </MapContainer>
      <div className="basemap-toggle" role="group" aria-label="Basemap">
        <button type="button" className={base === "street" ? "on" : ""} onClick={() => setBase("street")}>Street</button>
        <button type="button" className={base === "satellite" ? "on" : ""} onClick={() => setBase("satellite")}>Satellite</button>
        {base === "satellite" ? (
          <label>
            <input type="checkbox" checked={labels} onChange={(event) => setLabels(event.target.checked)} />
            Labels
          </label>
        ) : null}
      </div>
    </div>
  );
}

function MapBinder({ mapRef }: { mapRef: RefObject<L.Map | null> }) {
  const map = useMap();
  useEffect(() => {
    mapRef.current = map;
    return () => {
      mapRef.current = null;
    };
  }, [map, mapRef]);
  return null;
}

function LabelRefresh({ items }: { items: RefObject<Labeled[]> }) {
  const map = useMap();
  useEffect(() => {
    const refresh = () => {
      const zoom = map.getZoom();
      for (const { layer, minZoom } of items.current ?? []) {
        const attached = (layer as L.Layer & { _map?: L.Map | null })._map;
        if (attached && zoom >= minZoom) layer.openTooltip();
        else layer.closeTooltip();
      }
    };
    map.on("zoomend overlayadd layeradd", refresh);
    const frame = requestAnimationFrame(refresh);
    return () => {
      cancelAnimationFrame(frame);
      map.off("zoomend overlayadd layeradd", refresh);
    };
  }, [items, map]);
  return null;
}

function FitFrame({ geometry }: { geometry: GeoJSON.Geometry | null }) {
  const map = useMap();
  const fitted = useRef(false);
  useEffect(() => {
    if (fitted.current) return;
    fitted.current = true;
    const bounds = geometry
      ? L.geoJSON(feature(geometry)).getBounds()
      : L.latLngBounds([6.5, 68], [37.5, 97.5]);
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [20, 20] });
  }, [geometry, map]);
  return null;
}

function MapChrome() {
  const map = useMap();
  useEffect(() => {
    const hud = new L.Control({ position: "bottomleft" });
    hud.onAdd = () => {
      const el = L.DomUtil.create("div", "map-hud");
      const paint = (latlng?: L.LatLng) => {
        el.textContent = latlng ? `${latlng.lat.toFixed(3)}°  ${latlng.lng.toFixed(3)}°  ·  Z${map.getZoom()}` : `Z${map.getZoom()}`;
      };
      map.on("mousemove", (event) => paint(event.latlng));
      map.on("zoomend", () => paint());
      paint();
      return el;
    };
    const legend = new L.Control({ position: "bottomright" });
    legend.onAdd = () => {
      const el = L.DomUtil.create("div", "legend");
      el.innerHTML = `<b>PICTURE</b>
        <div><i style="background:#1d4e89"></i>Base</div>
        <div><i style="background:#ffd60a"></i>Target</div>
        <div><i style="background:#d00000;border-radius:99px"></i>Threat</div>
        <div><i class="ring"></i>Detect / engage</div>
        <div><i style="background:#0b3a75;clip-path:polygon(50% 0,100% 100%,0 100%)"></i>ADS-B</div>`;
      return el;
    };
    const scale = L.control.scale({ imperial: false, maxWidth: 140 });
    hud.addTo(map);
    legend.addTo(map);
    scale.addTo(map);
    return () => {
      hud.remove();
      legend.remove();
      scale.remove();
    };
  }, [map]);
  return null;
}

function MapLayers({ scenario }: { scenario: Scenario | null }) {
  return (
    <LayersControl position="topright" collapsed>
      {scenario ? <ScenarioOverlays scenario={scenario} /> : null}
    </LayersControl>
  );
}

function ScenarioOverlays({ scenario }: { scenario: Scenario }) {
  const byKind = (kind: string) => scenario.india_layers.filter((item) => item.kind === kind);
  const threatStatus = Object.fromEntries(scenario.threats.map((threat) => [String(threat.threat_id), String(threat.status)]));
  const tracks = new Map<string, [number, number][]>();
  for (const point of scenario.tracks) {
    const line = tracks.get(point.threat_id) ?? [];
    line.push([point.lat, point.lon]);
    tracks.set(point.threat_id, line);
  }
  return (
    <>
      <GeoLines name="Railways" items={byKind("rail")} style={{ color: "#4a4a4a", weight: 1.6, dashArray: "5 3" }} popup={() => "Railway"} />
      <GeoLines name="Highways" items={byKind("road")} style={{ color: "#c2762b", weight: 1.4 }} popup={(item) => `Road: ${text(item.name)}`} />
      <GeoLines name="Rivers" items={byKind("river")} style={{ color: "#2b8cd6", weight: 1.3 }} popup={(item) => `River: ${text(item.name)}`} label={(item) => text(item.name)} minZoom={6} />
      <GeoLines name="State boundaries" items={byKind("state")} style={{ color: "#7a8aa0", weight: 1, dashArray: "2 4" }} popup={() => "State boundary"} />
      <LayersControl.Overlay checked name="Cities">
        <LayerGroup>
          {byKind("city").map((city) => (
            <CityMarker key={text(city.name)} city={city} />
          ))}
        </LayerGroup>
      </LayersControl.Overlay>
      {scenario.india ? (
        <GeoLines name="India" items={[{ kind: "outline", name: "India", geometry: scenario.india }]} style={{ color: "#142033", weight: 2.5, fillColor: "#1d4e89", fillOpacity: 0.04 }} popup={() => "India"} />
      ) : null}
      <LayersControl.Overlay checked name="Bases">
        <LayerGroup>
          {scenario.bases.map((base) => (
            <StampedMarker key={text(base.base_id)} position={[num(base.lat), num(base.lon)]} icon={baseIcon} label={text(base.icao)} minZoom={5} popup={tip(base, ["base_id", "name", "icao", "elevation_ft", "runway_length_m", "shelter_count", "fuel_capacity_l", "max_sorties_per_hour"])} />
          ))}
        </LayerGroup>
      </LayersControl.Overlay>
      <GeoLines name="Borders" items={asLayers(scenario.borders)} style={{ color: "#c1121f", weight: 2.5 }} popup={(item) => text(item.name)} label={(item) => text(item.name)} minZoom={6} />
      <GeoLines
        name="GIS features"
        items={asLayers(scenario.features)}
        styleFor={(item) => ({ color: item.kind === "road" ? "#8a6a32" : item.kind === "city" ? "#1d4e89" : "#2b6cb0", weight: item.kind === "road" ? 2 : 1.5 })}
        popup={(item) => `${text(item.kind)}: ${text(item.name)}`}
        label={(item) => text(item.name)}
        minZoom={8}
      />
      <GeoLines
        name="Airspace"
        items={asLayers(scenario.zones)}
        styleFor={(item) => ({ color: ZONE_COLOR[text(item.type)] || "#555", weight: 1.5, fillOpacity: 0.08 })}
        popup={(item) => tip(item, ["zone_id", "name", "type", "floor_ft", "ceiling_ft", "active_from", "active_to"])}
        label={(item) => text(item.name)}
        minZoom={8}
      />
      <LayersControl.Overlay checked name="Threats">
        <LayerGroup>
          {scenario.threats.map((threat) => {
            const color = THREAT_COLOR[text(threat.status)] || "#d00000";
            const position: [number, number] = [num(threat.lat), num(threat.lon)];
            return (
              <LayerGroup key={text(threat.threat_id)}>
                <Circle center={position} radius={num(threat.detection_range_km) * 1000} pathOptions={{ color, weight: 1, dashArray: "2 6", fill: false, opacity: 0.35 }} />
                <Circle center={position} radius={num(threat.engagement_range_km) * 1000} pathOptions={{ color, weight: 1.5, fillOpacity: 0.06 }} />
                <StampedMarker position={position} icon={threatIcon(color)} label={`${THREAT_SHORT[text(threat.type)] || text(threat.type)} ${text(threat.threat_id)}`} minZoom={6} popup={tip(threat, ["threat_id", "type", "status", "engagement_range_km", "detection_range_km", "confidence"])} />
              </LayerGroup>
            );
          })}
        </LayerGroup>
      </LayersControl.Overlay>
      <LayersControl.Overlay checked name="Threat tracks">
        <LayerGroup>
          {[...tracks.entries()].map(([id, points]) => (
            <Polyline key={id} positions={points} pathOptions={{ color: THREAT_COLOR[threatStatus[id]] || "#d00000", weight: 1.5, opacity: 0.85 }} />
          ))}
        </LayerGroup>
      </LayersControl.Overlay>
      <LayersControl.Overlay checked name="Targets">
        <LayerGroup>
          {scenario.targets.map((target) => (
            <StampedMarker key={text(target.target_id)} position={[num(target.lat), num(target.lon)]} icon={targetIcon} label={`${text(target.target_id)} ${text(target.target_class)}`} minZoom={6} popup={tip(target, ["target_id", "target_class", "value", "hardness"])} />
          ))}
        </LayerGroup>
      </LayersControl.Overlay>
      <GeoLines
        name="Mission areas"
        items={scenario.missions.filter((mission) => mission.area).map((mission) => ({ ...mission, kind: "mission", geometry: mission.area as GeoJSON.Geometry }))}
        styleFor={(item) => ({ color: MISSION_COLOR[text(item.type)] || "#06d6a0", weight: 1.5, dashArray: "5 4", fillOpacity: 0.05 })}
        popup={(item) => tip(item, ["mission_id", "type", "priority", "status"])}
        label={(item) => `${text(item.mission_id)} ${text(item.type)}`}
        minZoom={8}
      />
      <LayersControl.Overlay name="Weather (flyability)">
        <LayerGroup>
          {scenario.weather.map(([lat, lon, flyability, storm], index) => (
            <Rectangle
              key={`${lat}:${lon}:${index}`}
              bounds={[[lat - 0.25, lon - 0.25], [lat + 0.25, lon + 0.25]]}
              pathOptions={{ stroke: false, fillColor: flyColor(flyability), fillOpacity: storm ? 0.7 : 0.3 }}
            />
          ))}
        </LayerGroup>
      </LayersControl.Overlay>
      <LayersControl.Overlay checked name="ADS-B">
        <LayerGroup>
          {scenario.adsb.map((track) => {
            const position: [number, number] = [num(track.lat), num(track.lon)];
            const label = `${text(track.callsign || track.icao24).trim()}${flightLevel(track.alt_m)}`;
            const popup = tip(track, ["icao24", "callsign", "alt_m", "speed_ms", "heading", "on_ground"]);
            if (track.on_ground) {
              return <CircleMarker key={text(track.icao24)} center={position} radius={3} pathOptions={{ color: "#8aa0b8", weight: 1, fillOpacity: 0.8 }}><Popup><span dangerouslySetInnerHTML={{ __html: popup }} /></Popup></CircleMarker>;
            }
            return <StampedMarker key={text(track.icao24)} position={position} icon={planeIcon(num(track.heading))} label={label} minZoom={7} popup={popup} />;
          })}
        </LayerGroup>
      </LayersControl.Overlay>
    </>
  );
}

function GeoLines({
  name,
  items,
  style,
  styleFor,
  popup,
  label,
  minZoom = 99,
}: {
  name: string;
  items: IndiaLayer[];
  style?: L.PathOptions;
  styleFor?: (item: IndiaLayer) => L.PathOptions;
  popup: (item: IndiaLayer) => string;
  label?: (item: IndiaLayer) => string;
  minZoom?: number;
}) {
  const stamp = useContext(LabelCtx);
  const data = useMemo<GeoJSON.FeatureCollection>(() => ({
    type: "FeatureCollection",
    features: items.filter((item) => item.geometry).map((item) => ({
      type: "Feature",
      properties: item,
      geometry: item.geometry as GeoJSON.Geometry,
    })),
  }), [items]);
  if (!data.features.length) return null;
  return (
    <LayersControl.Overlay checked name={name}>
      <GeoJSON
        data={data}
        style={(feature) => styleFor?.(feature?.properties as IndiaLayer) ?? style ?? {}}
        pointToLayer={(feature, latlng) => {
          const props = feature.properties as IndiaLayer;
          return L.circleMarker(latlng, { radius: props.kind === "city" ? 4 : 3, ...(styleFor?.(props) ?? style) });
        }}
        onEachFeature={(feature, layer) => {
          const props = feature.properties as IndiaLayer;
          layer.bindPopup(popup(props));
          if (label) stamp(layer, label(props), minZoom);
        }}
      />
    </LayersControl.Overlay>
  );
}

function CityMarker({ city }: { city: IndiaLayer }) {
  const stamp = useContext(LabelCtx);
  const [lon, lat] = point(city.geometry);
  const rank = city.rank ?? 9;
  return (
    <CircleMarker
      center={[lat, lon]}
      radius={rank <= 2 ? 5 : 3}
      pathOptions={{ color: "#fff", weight: 1, fillColor: "#1d4e89", fillOpacity: 0.9 }}
      eventHandlers={{
        add: (event) => stamp(event.target, text(city.name), rank <= 1 ? 4 : rank <= 3 ? 6 : 8),
      }}
    >
      <Popup>
        {text(city.name)}
        <br />
        pop {Number(city.pop ?? 0).toLocaleString()}
      </Popup>
    </CircleMarker>
  );
}

function StampedMarker({
  position,
  icon,
  label,
  minZoom,
  popup,
}: {
  position: [number, number];
  icon: L.DivIcon;
  label?: string;
  minZoom: number;
  popup: string;
}) {
  const stamp = useContext(LabelCtx);
  return (
    <Marker
      position={position}
      icon={icon}
      eventHandlers={{ add: (event) => stamp(event.target, label, minZoom) }}
    >
      <Popup>
        <span dangerouslySetInnerHTML={{ __html: popup }} />
      </Popup>
    </Marker>
  );
}

function asLayers(rows: Row[]): IndiaLayer[] {
  return rows.map((row) => ({ ...row, kind: text(row.kind), name: text(row.name), geometry: (row.geometry as GeoJSON.Geometry | null) ?? null }));
}

function feature(geometry: GeoJSON.Geometry): GeoJSON.Feature {
  return { type: "Feature", properties: {}, geometry };
}

function point(geometry: GeoJSON.Geometry | null): [number, number] {
  if (geometry && geometry.type === "Point") return geometry.coordinates as [number, number];
  return [0, 0];
}

function threatIcon(color: string) {
  return L.divIcon({ className: "pin threat", iconSize: [13, 13], iconAnchor: [6, 6], html: `<i style="background:${color}"></i>` });
}

function planeIcon(heading: number) {
  return L.divIcon({
    className: "ac",
    iconSize: [16, 16],
    iconAnchor: [8, 8],
    html: `<span style="transform:rotate(${Math.round(heading || 0)}deg)"></span>`,
  });
}

function flyColor(value: number) {
  return `hsl(${Math.round(value * 120)},70%,50%)`;
}

function flightLevel(meters: unknown) {
  return typeof meters === "number" ? ` FL${String(Math.round((meters * 3.28084) / 100)).padStart(3, "0")}` : "";
}

function num(value: unknown) {
  return typeof value === "number" ? value : Number(value ?? 0);
}

function text(value: unknown) {
  return value == null ? "" : String(value);
}

function tip(row: Row, keys: string[]) {
  return keys.map((key) => `<b>${escapeHtml(key)}</b>: ${escapeHtml(row[key])}`).join("<br>");
}

function escapeHtml(value: unknown) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char] ?? char);
}
