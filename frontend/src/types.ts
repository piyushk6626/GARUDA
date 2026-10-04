export type Row = Record<string, unknown>;

export type IndiaLayer = {
  kind: string;
  name?: string;
  geometry: GeoJSON.Geometry | null;
  rank?: number;
  pop?: number;
  [key: string]: unknown;
};

export type Scenario = {
  seed: number;
  counts: Record<string, number>;
  summary: {
    aircraft_status: Record<string, number>;
    crew_medical: Record<string, number>;
    mission_types: Record<string, number>;
  };
  weather_time: string;
  weather: [number, number, number, boolean][];
  india: GeoJSON.Geometry | null;
  india_layers: IndiaLayer[];
  borders: Row[];
  features: Row[];
  zones: Row[];
  bases: Row[];
  threats: Row[];
  tracks: { threat_id: string; lat: number; lon: number }[];
  targets: Row[];
  missions: Row[];
  observations: Row[];
  adsb: Row[];
  aircraft: Row[];
  crew: Row[];
  weapons: Row[];
  stocks: { base_id: string; quantity: number }[];
  load_configs: Row[];
};

export type WorkPlan = {
  goal: string;
  version: number;
  steps: { id: string; agent: string; objective: string; success_criteria: string[] }[];
};

export type ChatEvent =
  | { type: "session"; session_id: string }
  | { type: "phase"; phase: string; mode: string | null }
  | { type: "tool_start"; name: string; args: Record<string, unknown> }
  | { type: "tool_end"; name: string; ok: boolean; error: string | null }
  | { type: "step_start"; step_id: string; agent: string; objective: string }
  | { type: "step_end"; step_id: string; agent: string; status: string }
  | { type: "plan_proposed"; kind: string; plan: WorkPlan | null }
  | { type: "question"; question: string; phase: string }
  | { type: "final"; status: string; mode: string | null; answer: string }
  | { type: "done"; status: string }
  | { type: "error"; error: string };
