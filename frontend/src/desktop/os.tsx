import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, events, type User } from "../lib/api";
import { APPS, type AppDef, type AppId } from "../apps/registry";

export type Win = {
  id: string;
  app: AppId;
  title: string;
  props: Record<string, unknown>;
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
  minimized: boolean;
  maximized: boolean;
};

export type Note = { id: number; title: string; body?: string; kind: "info" | "ok" | "error" | "ai"; ts: number; read: boolean };

type OS = {
  user: User;
  setUser: (u: User) => void;
  updateSettings: (s: Record<string, unknown>) => Promise<void>;
  windows: Win[];
  open: (app: AppId, props?: Record<string, unknown>) => string;
  close: (id: string) => void;
  focus: (id: string) => void;
  minimize: (id: string) => void;
  toggleMax: (id: string) => void;
  patch: (id: string, p: Partial<Win>) => void;
  activeId: string | null;
  notes: Note[];
  notify: (title: string, body?: string, kind?: Note["kind"]) => void;
  dismiss: (id: number) => void;
  clearNotes: () => void;
  isMobile: boolean;
  logout: () => Promise<void>;
  launcherOpen: boolean;
  setLauncherOpen: (v: boolean) => void;
};

const Ctx = createContext<OS | null>(null);

export const useOS = () => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useOS outside provider");
  return v;
};

let seq = 0;
const LAYOUT_KEY = "murcloud.layout.v1";

function useIsMobile() {
  const [m, setM] = useState(() => window.innerWidth < 768);
  useEffect(() => {
    const on = () => setM(window.innerWidth < 768);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return m;
}

export function OSProvider({ initialUser, onLogout, children }: { initialUser: User; onLogout: () => void; children: ReactNode }) {
  const [user, setUser] = useState(initialUser);
  const [windows, setWindows] = useState<Win[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [launcherOpen, setLauncherOpen] = useState(false);
  const zTop = useRef(10);
  const isMobile = useIsMobile();

  const open = useCallback((app: AppId, props: Record<string, unknown> = {}) => {
    const def: AppDef = APPS[app];
    let id = "";
    setWindows((ws) => {
      if (def.singleton) {
        const existing = ws.find((w) => w.app === app);
        if (existing) {
          id = existing.id;
          zTop.current += 1;
          return ws.map((w) => (w.id === existing.id ? { ...w, minimized: false, z: zTop.current, props: { ...w.props, ...props } } : w));
        }
      }
      id = `w${++seq}`;
      zTop.current += 1;
      const vw = window.innerWidth;
      const vh = window.innerHeight - 64;
      const w = Math.min(def.size[0], vw - 24);
      const h = Math.min(def.size[1], vh - 24);
      const offset = (ws.length % 6) * 28;
      return [
        ...ws,
        {
          id,
          app,
          title: def.title,
          props,
          x: Math.max(12, Math.min((vw - w) / 2 + offset - 60, vw - w - 12)),
          y: Math.max(12, Math.min((vh - h) / 2 + offset - 40, vh - h)),
          w,
          h,
          z: zTop.current,
          minimized: false,
          maximized: false,
        },
      ];
    });
    return id;
  }, []);

  const close = useCallback((id: string) => setWindows((ws) => ws.filter((w) => w.id !== id)), []);
  const focus = useCallback((id: string) => {
    zTop.current += 1;
    const z = zTop.current;
    setWindows((ws) => ws.map((w) => (w.id === id ? { ...w, z, minimized: false } : w)));
  }, []);
  const minimize = useCallback((id: string) => setWindows((ws) => ws.map((w) => (w.id === id ? { ...w, minimized: true } : w))), []);
  const toggleMax = useCallback((id: string) => setWindows((ws) => ws.map((w) => (w.id === id ? { ...w, maximized: !w.maximized } : w))), []);
  const patch = useCallback((id: string, p: Partial<Win>) => setWindows((ws) => ws.map((w) => (w.id === id ? { ...w, ...p } : w))), []);

  const notify = useCallback((title: string, body?: string, kind: Note["kind"] = "info") => {
    const n: Note = { id: Date.now() + Math.random(), title, body, kind, ts: Date.now(), read: false };
    setNotes((ns) => [n, ...ns].slice(0, 50));
  }, []);
  const dismiss = useCallback((id: number) => setNotes((ns) => ns.map((n) => (n.id === id ? { ...n, read: true } : n))), []);
  const clearNotes = useCallback(() => setNotes([]), []);

  const updateSettings = useCallback(async (s: Record<string, unknown>) => {
    const u = await api.patch<User>("/api/settings", { settings: s });
    setUser(u);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      events.disconnect();
      onLogout();
    }
  }, [onLogout]);

  // Restore the window layout after reload (apps reopen; their data comes from the server).
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "[]") as Win[];
      if (Array.isArray(saved) && saved.length) {
        const valid = saved.filter((w) => w.app in APPS).slice(0, 8);
        seq = valid.length + 1;
        zTop.current = Math.max(10, ...valid.map((w) => w.z)) + 1;
        setWindows(valid);
      } else {
        open("studio");
      }
    } catch {
      open("studio");
    }
  }, [open]);
  useEffect(() => {
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(windows));
    } catch {
      /* storage unavailable */
    }
  }, [windows]);

  // Global notifications from live events.
  useEffect(() => {
    const offs = [
      events.on("run.finished", (e) => {
        const run = e.run as { file: string; status: string; error?: { message: string } };
        notify(`Сценарий ${run.file}: ${run.status === "done" ? "готово" : run.status}`, run.error?.message, run.status === "done" ? "ok" : "error");
      }),
      events.on("ai.approval_required", (e) => {
        const a = e.approval as { tool: string; risk: string };
        notify("MUR AI ждёт подтверждения", `${a.tool} (${a.risk} risk)`, "ai");
      }),
      events.on("browser.download_finished", (e) => notify("Загрузка завершена", `${e.filename} → MUR Drive/Downloads`, "ok")),
      events.on("package.installed", (e) => notify("Пакет установлен", String(e.package), "ok")),
      events.on("drive.import_failed", (e) => notify("Не удалось сохранить в MUR Drive", `${e.file}: ${e.reason}`, "error")),
    ];
    return () => offs.forEach((o) => o());
  }, [notify]);

  // Hotkeys.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const top = [...windows].filter((w) => !w.minimized).sort((a, b) => b.z - a.z)[0];
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
        ev.preventDefault();
        setLauncherOpen((v) => !v);
      } else if (ev.altKey && ev.code === "Space") {
        ev.preventDefault();
        setLauncherOpen((v) => !v);
      } else if (ev.key === "Escape" && launcherOpen) {
        setLauncherOpen(false);
      } else if (ev.altKey && top) {
        const k = ev.key.toLowerCase();
        if (k === "q") {
          ev.preventDefault();
          close(top.id);
        } else if (k === "m") {
          ev.preventDefault();
          minimize(top.id);
        } else if (ev.key === "Enter") {
          ev.preventDefault();
          toggleMax(top.id);
        } else if (ev.key === "`" || ev.code === "Backquote") {
          ev.preventDefault();
          const sorted = [...windows].sort((a, b) => a.z - b.z);
          if (sorted.length > 1) focus(sorted[0].id);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [windows, launcherOpen, close, minimize, toggleMax, focus]);

  const activeId = useMemo(() => {
    const vis = windows.filter((w) => !w.minimized);
    return vis.length ? vis.reduce((a, b) => (a.z > b.z ? a : b)).id : null;
  }, [windows]);

  const value: OS = {
    user,
    setUser,
    updateSettings,
    windows,
    open,
    close,
    focus,
    minimize,
    toggleMax,
    patch,
    activeId,
    notes,
    notify,
    dismiss,
    clearNotes,
    isMobile,
    logout,
    launcherOpen,
    setLauncherOpen,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
