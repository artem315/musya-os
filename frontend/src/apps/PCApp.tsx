import { Maximize, MonitorPlay, Play, Plug, PlugZap, Power, RotateCw, Server, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useOS } from "../desktop/os";
import { api, API_BASE, formatBytes } from "../lib/api";

type Session = { id: string; name: string; status: string; created_at: number; resolution: string };
type Avail = { available: boolean; reason?: string; image?: string };
type Stats = { status: string; cpu_percent?: number; memory_bytes?: number; memory_limit?: number; pids?: number };

const APPS: [string, string][] = [
  ["terminal", "Терминал"],
  ["browser", "Firefox"],
  ["files", "Файлы"],
  ["editor", "Редактор"],
  ["taskmanager", "Диспетчер задач"],
  ["games", "Игры (SGT Puzzles)"],
];

export default function PCApp() {
  const os = useOS();
  const [avail, setAvail] = useState<Avail | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [stats, setStats] = useState<Stats | null>(null);
  const [resolution, setResolution] = useState("1280x800");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const screen = useRef<HTMLDivElement>(null);
  const rfb = useRef<import("@novnc/novnc").default | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ availability: Avail; sessions: Session[] }>("/api/pc");
      setAvail(r.availability);
      setSessions(r.sessions);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const disconnect = () => {
    rfb.current?.disconnect();
    rfb.current = null;
    setConnected(false);
  };
  useEffect(() => disconnect, []);

  const connect = async (id: string) => {
    disconnect();
    setError(null);
    setCurrent(id);
    try {
      const c = await api.get<{ ws_path: string; password: string }>(`/api/pc/${id}/connect`);
      const { default: RFB } = await import("@novnc/novnc");
      const base = (API_BASE || window.location.origin).replace(/^http/, "ws");
      const r = new RFB(screen.current!, base + c.ws_path, { credentials: { password: c.password }, wsProtocols: ["binary"] });
      r.scaleViewport = true;
      r.resizeSession = false;
      r.background = "#05050f";
      r.addEventListener("connect", () => setConnected(true));
      r.addEventListener("disconnect", () => setConnected(false));
      r.addEventListener("securityfailure", () => setError("VNC: ошибка аутентификации"));
      rfb.current = r;
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    if (!current) return;
    const t = setInterval(() => {
      api.get<Stats>(`/api/pc/${current}/stats`).then(setStats).catch(() => undefined);
    }, 3000);
    return () => clearInterval(t);
  }, [current]);

  const action = async (id: string, a: string) => {
    setBusy(true);
    try {
      if (a === "delete" || a === "stop") disconnect();
      await api.post(`/api/pc/${id}/${a}`);
      await load();
      if (a === "start" || a === "restart") setTimeout(() => connect(id), 2500);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await api.post<Session>("/api/pc", { name: "MUR PC", resolution });
      await load();
      os.notify("MUR PC создаётся", "Рабочий стол будет готов через несколько секунд.", "info");
      setTimeout(() => connect(s.id), 4000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!avail) return <div className="grid h-full place-items-center muted">Проверка MUR PC…</div>;

  if (!avail.available)
    return (
      <div className="h-full overflow-auto p-6">
        <div className="mx-auto max-w-lg space-y-3">
          <div className="flex items-center gap-2 text-lg font-semibold">
            <Server className="text-pink-300" /> MUR PC недоступен на этом сервере
          </div>
          <div className="rounded-2xl border border-amber-400/40 bg-amber-400/10 p-3 text-sm">{avail.reason}</div>
          <div className="text-sm muted">
            MUR PC — это настоящий Linux-рабочий стол (XFCE в изолированном Docker-контейнере, VNC через защищённый WebSocket-прокси). Для него нужен сервер
            с Docker (VPS). Render и похожие PaaS не позволяют запускать контейнеры.
          </div>
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            <li>
              Соберите образ: <code className="font-mono">docker build -t murcloud/mur-pc:10.0 remote-desktop/</code>
            </li>
            <li>
              Запустите backend с <code className="font-mono">MUR_PC_ENABLED=1</code> и доступом к Docker.
            </li>
            <li>См. remote-desktop/README.md.</li>
          </ol>
        </div>
      </div>
    );

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="pc">
      <div className="flex flex-wrap items-center gap-1.5 border-b p-1.5 text-xs" style={{ borderColor: "var(--panel-border)" }}>
        <select className="input !w-auto !py-1 !text-xs" value={resolution} onChange={(e) => setResolution(e.target.value)}>
          {["1024x768", "1280x800", "1366x768", "1600x900", "1920x1080"].map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <button className="btn btn-sm btn-primary" onClick={create} disabled={busy}>
          <MonitorPlay size={13} /> Новый MUR PC
        </button>
        {sessions.map((s) => (
          <div key={s.id} className={`flex items-center gap-1 rounded-xl px-1.5 py-0.5 ${current === s.id ? "bg-white/10" : ""}`}>
            <span className={`h-2 w-2 rounded-full ${s.status === "running" ? "bg-emerald-400" : "bg-zinc-500"}`} />
            <button className="font-mono" onClick={() => s.status === "running" && connect(s.id)}>
              {s.name} {s.id.slice(0, 6)}
            </button>
            {s.status === "running" ? (
              <>
                <button className="btn btn-sm btn-ghost !px-1" title="Переподключиться" onClick={() => connect(s.id)}>
                  <Plug size={12} />
                </button>
                <button className="btn btn-sm btn-ghost !px-1" title="Перезагрузить" onClick={() => action(s.id, "restart")}>
                  <RotateCw size={12} />
                </button>
                <button className="btn btn-sm btn-ghost !px-1" title="Выключить" onClick={() => action(s.id, "stop")}>
                  <Power size={12} />
                </button>
              </>
            ) : (
              <button className="btn btn-sm btn-ghost !px-1" title="Включить" onClick={() => action(s.id, "start")}>
                <Play size={12} />
              </button>
            )}
            <button className="btn btn-sm btn-ghost !px-1" title="Удалить" onClick={() => confirm("Удалить этот MUR PC вместе с данными внутри?") && action(s.id, "delete")}>
              <Trash2 size={12} />
            </button>
          </div>
        ))}
        <div className="ml-auto flex items-center gap-2">
          {stats?.cpu_percent != null && (
            <span className="muted">
              CPU {stats.cpu_percent}% · RAM {formatBytes(stats.memory_bytes ?? 0)} / {formatBytes(stats.memory_limit ?? 0)}
            </span>
          )}
          {connected ? <PlugZap size={14} className="text-emerald-300" /> : <Plug size={14} className="muted" />}
        </div>
      </div>
      {connected && current && (
        <div className="flex flex-wrap gap-1 border-b p-1 text-xs" style={{ borderColor: "var(--panel-border)" }}>
          {APPS.map(([k, label]) => (
            <button key={k} className="btn btn-sm" onClick={() => api.post(`/api/pc/${current}/launch/app`, { app: k }).catch((e) => setError(e.message))}>
              {label}
            </button>
          ))}
          <button className="btn btn-sm" onClick={() => rfb.current?.sendCtrlAltDel()}>
            Ctrl+Alt+Del
          </button>
          <button className="btn btn-sm" onClick={() => screen.current?.requestFullscreen()}>
            <Maximize size={12} /> Полный экран
          </button>
        </div>
      )}
      <div className="relative min-h-0 flex-1 bg-[#05050f]">
        <div ref={screen} className="absolute inset-0" />
        {!connected && (
          <div className="absolute inset-0 grid place-items-center p-6 text-center text-sm muted">
            {sessions.length === 0 ? "Создайте MUR PC — это займёт пару секунд." : "Выберите запущенный MUR PC, чтобы подключиться."}
          </div>
        )}
      </div>
      {error && <div className="border-t p-1.5 text-xs text-rose-300" style={{ borderColor: "var(--panel-border)" }}>{error}</div>}
    </div>
  );
}
