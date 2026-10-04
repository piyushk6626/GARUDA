import { useMemo, useState } from "react";

import { BADGE, METER, PALETTE, WRAP, badgeTone, columnLabel, compare, formatNumber, formatTime, meterColor } from "../format";
import type { Row, Scenario } from "../types";

const TABS = [
  "Missions",
  "Aircraft",
  "Crew",
  "Weapons",
  "Loads",
  "Threats",
  "Airspace",
  "Weather",
  "ADS-B",
  "Bases",
] as const;

type TabName = (typeof TABS)[number];

export function DataPanel({ scenario }: { scenario: Scenario | null }) {
  const [tab, setTab] = useState<TabName>("Missions");
  return (
    <aside className="side">
      <nav>
        {TABS.map((name) => (
          <button key={name} type="button" className={name === tab ? "on" : ""} aria-pressed={name === tab} onClick={() => setTab(name)}>
            {name}
          </button>
        ))}
      </nav>
      <div className="panel">
        {scenario ? <TabBody name={tab} scenario={scenario} /> : <p className="note">Loading the scenario…</p>}
      </div>
    </aside>
  );
}

function TabBody({ name, scenario }: { name: TabName; scenario: Scenario }) {
  const stock = useMemo(() => stockByBase(scenario.stocks), [scenario]);
  if (name === "Missions") {
    return (
      <>
        <Bars title="Mission types" counter={scenario.summary.mission_types} />
        <DataTable title="Missions" rows={scenario.missions} cols={["mission_id", "type", "priority", "window_start", "duration_min", "target_id", "status"]} />
      </>
    );
  }
  if (name === "Aircraft") {
    return (
      <>
        <Bars title="Aircraft status" counter={scenario.summary.aircraft_status} />
        <DataTable title="Aircraft" rows={scenario.aircraft} cols={["tail_no", "type", "base_id", "status", "status_reason", "fuel_kg", "hours_to_next_inspection"]} />
      </>
    );
  }
  if (name === "Crew") {
    return (
      <>
        <Bars title="Medical status" counter={scenario.summary.crew_medical} />
        <DataTable title="Crew" rows={scenario.crew} cols={["crew_id", "callsign", "role", "base_id", "primary_type", "medical_status", "fatigue_score", "duty_hours_24h"]} />
      </>
    );
  }
  if (name === "Weapons") {
    return (
      <>
        <DataTable title="Weapon types" rows={scenario.weapons} cols={["weapon_code", "category", "range_km", "weight_kg", "guidance"]} />
        <DataTable title="Stock by base" rows={stock} cols={["base_id", "weapon_types", "rounds"]} />
      </>
    );
  }
  if (name === "Loads") {
    return <DataTable title="Loadouts" rows={scenario.load_configs} cols={["config_id", "aircraft_type", "role", "total_weight_kg", "range_penalty_pct"]} />;
  }
  if (name === "Threats") {
    return <DataTable title="Threats" rows={scenario.threats} cols={["threat_id", "type", "status", "mobile", "engagement_range_km", "pk_base", "confidence"]} />;
  }
  if (name === "Airspace") {
    return <DataTable title="Zones" rows={scenario.zones} cols={["zone_id", "name", "type", "floor_ft", "ceiling_ft", "source", "capacity_per_hour"]} />;
  }
  if (name === "Weather") {
    return (
      <>
        <p className="note">Station observations for {formatTime(scenario.weather_time)}. On the map, color is flyability and a stronger fill marks thunderstorms.</p>
        <DataTable title="Observations" rows={scenario.observations} cols={["icao", "obs_time", "flight_category", "ceiling_ft", "vis_km", "wind_kts", "flyability"]} />
      </>
    );
  }
  if (name === "ADS-B") {
    return <DataTable title="Tracks" rows={scenario.adsb} cols={["icao24", "callsign", "origin_country", "alt_m", "speed_ms", "heading", "on_ground"]} />;
  }
  return <DataTable title="Bases" rows={scenario.bases} cols={["base_id", "name", "icao", "runway_count", "runway_length_m", "shelter_count", "fuel_capacity_l", "max_sorties_per_hour"]} />;
}

function stockByBase(stocks: Scenario["stocks"]): Row[] {
  const grouped = new Map<string, { base_id: string; weapon_types: number; rounds: number }>();
  for (const stock of stocks) {
    const row = grouped.get(stock.base_id) ?? { base_id: stock.base_id, weapon_types: 0, rounds: 0 };
    row.weapon_types += 1;
    row.rounds += stock.quantity;
    grouped.set(stock.base_id, row);
  }
  return [...grouped.values()];
}

function Bars({ title, counter }: { title: string; counter: Record<string, number> }) {
  const entries = Object.entries(counter).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(([, value]) => value), 1);
  return (
    <section className="summary">
      <h2>{title}</h2>
      <div className="bars">
        {entries.map(([key, value], index) => (
          <div className="bar-row" key={key}>
            <span className="bar-label" title={key}>{key.replace(/_/g, " ")}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(value / max) * 100}%`, background: PALETTE[index % PALETTE.length] }} />
            </span>
            <span className="bar-value">{value.toLocaleString("en-US")}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function DataTable({ title, rows, cols }: { title: string; rows: Row[]; cols: string[] }) {
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [asc, setAsc] = useState(true);
  const numeric = useMemo(() => new Set(cols.filter((col) => rows.some((row) => typeof row[col] === "number"))), [cols, rows]);
  const centered = useMemo(
    () => new Set(cols.filter((col) => col === "priority" || rows.some((row) => typeof row[col] === "boolean"))),
    [cols, rows],
  );
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matched = needle
      ? rows.filter((row) => cols.some((col) => String(row[col] ?? "").toLowerCase().includes(needle)))
      : rows;
    if (!sortKey) return matched;
    return matched.slice().sort((a, b) => {
      const order = compare(a[sortKey], b[sortKey]);
      const empty = a[sortKey] == null || a[sortKey] === "" || b[sortKey] == null || b[sortKey] === "";
      return order === 0 || empty ? order : order * (asc ? 1 : -1);
    });
  }, [asc, cols, query, rows, sortKey]);

  return (
    <section className="table-card">
      <div className="toolbar">
        <h2>{title}</h2>
        <input type="search" value={query} placeholder="Filter rows" aria-label={`Filter ${title}`} onChange={(event) => setQuery(event.target.value)} />
        <span className="count">{query.trim() ? `${shown.length} / ${rows.length}` : `${rows.length.toLocaleString("en-US")} rows`}</span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {cols.map((col) => {
                const className = [numeric.has(col) ? "num" : "", centered.has(col) ? "mid" : "", sortKey === col ? "sorted" : ""].filter(Boolean).join(" ");
                return (
                  <th
                    key={col}
                    className={className}
                    onClick={() => {
                      setAsc(sortKey === col ? !asc : true);
                      setSortKey(col);
                    }}
                  >
                    {columnLabel(col)}
                    {sortKey === col ? <span className="caret">{asc ? "▲" : "▼"}</span> : null}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.length ? (
              shown.map((row, index) => (
                <tr key={index}>
                  {cols.map((col, colIndex) => {
                    const className = [colIndex === 0 ? "id" : "", numeric.has(col) ? "num" : "", centered.has(col) ? "mid" : "", WRAP.has(col) ? "wrap" : ""].filter(Boolean).join(" ");
                    return (
                      <td key={col} className={className}>
                        <Cell name={col} value={row[col]} />
                      </td>
                    );
                  })}
                </tr>
              ))
            ) : (
              <tr className="empty-row">
                <td colSpan={cols.length}>No rows match</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Cell({ name, value }: { name: string; value: unknown }) {
  if (value === null || value === undefined || value === "") return <span className="dash">—</span>;
  if (typeof value === "boolean") {
    return value ? <span className="badge ok">Yes</span> : <span className="badge muted">No</span>;
  }
  if (name === "priority") return <span className={`pri p${String(value)}`}>{String(value)}</span>;
  if (METER.has(name) && typeof value === "number") {
    const pct = Math.max(0, Math.min(100, Math.round(value * 100)));
    return (
      <>
        <span className="meter" title={`${pct}%`}>
          <span style={{ width: `${pct}%`, background: meterColor(name, value) }} />
        </span>
        <span className="num">{value.toFixed(2)}</span>
      </>
    );
  }
  if (BADGE.has(name) && typeof value === "string") {
    return <span className={`badge ${badgeTone(value)}`}>{value.replace(/_/g, " ")}</span>;
  }
  if (typeof value === "number") {
    const text = name.endsWith("_pct") ? `${formatNumber(value)}%` : name === "duration_min" ? `${formatNumber(value)} min` : formatNumber(value);
    return <span className="num">{text}</span>;
  }
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)) return <span className="time">{formatTime(value)}</span>;
  return <>{String(value)}</>;
}
