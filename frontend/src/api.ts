import type { ChatEvent, Scenario } from "./types";

export async function fetchScenario(): Promise<Scenario> {
  const response = await fetch("/api/scenario");
  if (!response.ok) {
    throw new Error(`Scenario request failed (${response.status}). Start the API with make api.`);
  }
  return response.json() as Promise<Scenario>;
}

export async function streamChat(
  url: string,
  body: unknown,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || `Request failed (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const take = (chunk: string) => {
    buffer += chunk.replace(/\r\n/g, "\n");
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) emit(part, onEvent);
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    take(decoder.decode(value, { stream: true }));
  }
  take(decoder.decode());
  if (buffer.trim()) emit(buffer, onEvent);
}

function emit(part: string, onEvent: (event: ChatEvent) => void) {
  const data = part
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => line.slice(6))
    .join("\n");
  if (data) onEvent(JSON.parse(data) as ChatEvent);
}
