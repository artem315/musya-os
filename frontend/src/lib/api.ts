// HTTP + WebSocket client for the MURCLOUD backend.

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, message: string, data?: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

/** Base URL of the backend. Empty = same origin (normal web deployment and the Android WebView). */
export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

type Json = Record<string, unknown> | unknown[] | string | number | boolean | null;

async function request<T>(method: string, path: string, body?: Json | FormData, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { "X-MUR-CSRF": "1" };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(API_BASE + path, { method, headers, body: payload, credentials: "include", signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Нет связи с сервером MURCLOUD. Проверьте интернет.");
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const err = (data as { error?: { message?: string } } | null)?.error;
    let msg = err?.message ?? `HTTP ${res.status}`;
    if (Array.isArray((data as { detail?: unknown })?.detail)) {
      msg = ((data as { detail: { msg: string; loc: string[] }[] }).detail)
        .map((d) => `${d.loc.slice(-1)[0]}: ${d.msg}`)
        .join("; ");
    }
    throw new ApiError(res.status, msg, err ?? data);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, undefined, signal),
  post: <T>(path: string, body?: Json | FormData) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body?: Json) => request<T>("PUT", path, body ?? {}),
  patch: <T>(path: string, body?: Json) => request<T>("PATCH", path, body ?? {}),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export const q = (params: Record<string, string | number | boolean>) =>
  "?" + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();

// ------------------------------------------------------------------ live events

export type MurEvent = { type: string; ts?: number; [k: string]: unknown };
type Listener = (e: MurEvent) => void;

class EventStream {
  private ws: WebSocket | null = null;
  private listeners = new Map<string, Set<Listener>>();
  private retry = 0;
  private closed = true;
  private timer: number | undefined;
  status: "connecting" | "open" | "closed" = "closed";
  private statusListeners = new Set<(s: EventStream["status"]) => void>();

  connect() {
    this.closed = false;
    this.open();
  }

  private setStatus(s: EventStream["status"]) {
    this.status = s;
    this.statusListeners.forEach((l) => l(s));
  }

  private open() {
    if (this.closed) return;
    const base = API_BASE || window.location.origin;
    const url = base.replace(/^http/, "ws") + "/api/ws";
    this.setStatus("connecting");
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.setStatus("open");
    };
    ws.onmessage = (m) => {
      try {
        this.dispatch(JSON.parse(m.data as string));
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = (ev) => {
      this.ws = null;
      this.setStatus("closed");
      if (ev.code === 4401) return; // signed out
      if (!this.closed) {
        this.retry = Math.min(this.retry + 1, 6);
        this.timer = window.setTimeout(() => this.open(), 500 * 2 ** this.retry);
      }
    };
  }

  disconnect() {
    this.closed = true;
    window.clearTimeout(this.timer);
    this.ws?.close();
  }

  send(msg: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  private dispatch(e: MurEvent) {
    for (const [prefix, set] of this.listeners) {
      if (prefix === "*" || e.type === prefix || (prefix.endsWith(".") && e.type.startsWith(prefix))) {
        set.forEach((l) => l(e));
      }
    }
  }

  /** Subscribe to an exact type ("run.output"), a prefix ending with "." ("ai."), or "*". */
  on(prefix: string, fn: Listener): () => void {
    if (!this.listeners.has(prefix)) this.listeners.set(prefix, new Set());
    this.listeners.get(prefix)!.add(fn);
    return () => this.listeners.get(prefix)?.delete(fn);
  }

  onStatus(fn: (s: EventStream["status"]) => void): () => void {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }
}

export const events = new EventStream();

// ------------------------------------------------------------------ types

export type User = {
  id: string;
  username: string;
  display_name: string;
  is_admin: boolean;
  settings: Record<string, unknown>;
};

export type Project = {
  id: string;
  name: string;
  slug: string;
  description: string;
  template: string;
  updated_at: number;
  state?: Record<string, unknown>;
};

export type FileEntry = { path: string; name: string; is_dir: boolean; size: number; mtime: number };

export type Diagnostic = { severity: "error" | "warning"; line: number; column: number; message: string; file?: string };

export type Job = {
  id: string;
  verb: string;
  args: unknown[];
  source: string;
  status: string;
  command: string;
  result?: unknown;
  error?: { type: string; message: string } | null;
  duration_ms?: number | null;
};

export type DriveItem = {
  id: string;
  path: string;
  name: string;
  parent: string;
  is_dir: boolean;
  size: number;
  mime: string;
  updated_at: number;
};

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}
