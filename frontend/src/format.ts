export const LABELS: Record<string, string> = {
  mission_id: "Mission",
  window_start: "Window",
  duration_min: "Duration",
  target_id: "Target",
  tail_no: "Tail",
  base_id: "Base",
  status_reason: "Reason",
  fuel_kg: "Fuel kg",
  hours_to_next_inspection: "Hrs to insp.",
  crew_id: "Crew",
  primary_type: "Type",
  medical_status: "Medical",
  fatigue_score: "Fatigue",
  duty_hours_24h: "Duty 24h",
  weapon_code: "Weapon",
  range_km: "Range km",
  weight_kg: "Weight kg",
  weapon_types: "Types",
  config_id: "Config",
  aircraft_type: "Aircraft",
  total_weight_kg: "Weight kg",
  range_penalty_pct: "Penalty",
  threat_id: "Threat",
  engagement_range_km: "Range km",
  pk_base: "Pk",
  zone_id: "Zone",
  floor_ft: "Floor ft",
  ceiling_ft: "Ceiling ft",
  capacity_per_hour: "Cap / h",
  obs_time: "Observed",
  flight_category: "Category",
  vis_km: "Vis km",
  wind_kts: "Wind kt",
  origin_country: "Country",
  alt_m: "Alt m",
  speed_ms: "m/s",
  on_ground: "Ground",
  runway_count: "Runways",
  runway_length_m: "Length m",
  shelter_count: "Shelters",
  fuel_capacity_l: "Fuel L",
  max_sorties_per_hour: "Sorties / h",
  icao24: "ICAO24",
  priority: "Pri",
};

export const COUNT_LABEL: Record<string, string> = {
  missions: "Missions",
  targets: "Targets",
  threats: "Threats",
  bases: "Bases",
  aircraft: "Aircraft",
  crew: "Crew",
  zones: "Zones",
  adsb_tracks: "ADS-B",
  weapons: "Weapons",
  cells: "Wx cells",
  observations: "Obs",
};

export const COUNT_KEYS = [
  "missions",
  "targets",
  "threats",
  "bases",
  "aircraft",
  "crew",
  "zones",
  "adsb_tracks",
  "weapons",
  "cells",
  "observations",
];

const TONE: Record<string, string> = {
  FMC: "ok",
  FIT: "ok",
  VFR: "ok",
  DONE: "ok",
  APPROVED: "ok",
  EXECUTING: "ok",
  SAR: "ok",
  ACTIVE: "bad",
  NMC: "bad",
  AOG: "bad",
  GROUNDED: "bad",
  LIFR: "bad",
  CANCELLED: "bad",
  PROHIBITED: "bad",
  RESTRICTED: "bad",
  STRIKE: "bad",
  PMC: "warn",
  LIMITED: "warn",
  MVFR: "warn",
  SUPPRESSED: "warn",
  IFR: "warn",
  DANGER: "warn",
  REQUESTED: "warn",
  SEAD: "warn",
  CAS: "warn",
  PLANNED: "info",
  IN_MISSION: "info",
  CAP: "info",
  DCA: "info",
  ISR: "info",
  AAR: "info",
  ADIZ: "info",
  UNKNOWN: "muted",
  DESTROYED: "muted",
  synthetic: "muted",
  openaip: "info",
  metar: "info",
};

export const BADGE = new Set([
  "status",
  "medical_status",
  "flight_category",
  "type",
  "role",
  "category",
  "guidance",
  "source",
  "primary_type",
]);
export const METER = new Set(["flyability", "fatigue_score", "confidence", "pk_base"]);
export const WRAP = new Set(["name", "status_reason"]);
export const PALETTE = ["#1d4e89", "#0f766e", "#b45309", "#9f1239", "#6d28d9", "#0369a1", "#3f6212", "#9a3412"];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function columnLabel(key: string): string {
  return LABELS[key] || key.replace(/_/g, " ");
}

export function formatNumber(n: number): string {
  if (Number.isInteger(n)) return n.toLocaleString("en-US");
  const digits = Math.abs(n) >= 100 ? 1 : 2;
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getUTCDate())} ${MONTHS[date.getUTCMonth()]} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}z`;
}

export function meterColor(key: string, n: number): string {
  const hot = key === "fatigue_score" || key === "pk_base";
  const bad = hot ? n >= 0.66 : n < 0.34;
  const good = hot ? n < 0.34 : n >= 0.66;
  if (bad) return "#c0352b";
  if (good) return "#157f3d";
  return "#d4a017";
}

export function badgeTone(value: string): string {
  return TONE[value] || "neutral";
}

export function compare(a: unknown, b: unknown): number {
  const aEmpty = a === null || a === undefined || a === "";
  const bEmpty = b === null || b === undefined || b === "";
  if (aEmpty || bEmpty) return Number(aEmpty) - Number(bEmpty);
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "boolean" && typeof b === "boolean") return Number(a) - Number(b);
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}
