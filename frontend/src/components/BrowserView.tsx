import { ArrowLeft, ArrowRight, Camera, Loader2, Plus, Power, RotateCw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, events, type Job } from "../lib/api";

type Frame = { image: string; width: number; height: number; url: string; title: string };
type Tab = { index: number; url: string; title: string; active: boolean };

/** Live view and remote control of the server-side Chromium (MUR Browser and Studio's Browser tab). */
export function BrowserView({ fps = 2 }: { fps?: number }) {
  const [frame, setFrame] = useState<Frame | null>(null);
  const [address, setAddress] = useState("https://example.com");
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [running, setRunning] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState<Job[]>([]);
  const img = useRef<HTMLImageElement>(null);
  const focused = useRef(false);

  const refreshStatus = useCallback(async () => {
    try {
      const st = await api.get<{ running: boolean; tabs: Tab[] }>("/api/engine/status");
      setRunning(st.running);
      setTabs(st.tabs);
      const active = st.tabs.find((t) => t.active);
      if (active && !focused.current) setAddress(active.url);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refreshStatus();
    const offFrame = events.on("browser.frame", (e) => setFrame(e as unknown as Frame));
    const offJobs = events.on("job.", (e) => {
      const j = e.job as Job;
      if (e.type === "job.finished" || e.type === "job.failed" || e.type === "job.timeout") {
        setLog((l) => [j, ...l].slice(0, 30));
        refreshStatus();
      }
    });
    const offStart = events.on("browser.", (e) => {
      if (e.type === "browser.started" || e.type === "browser.stopped") refreshStatus();
    });
    return () => {
      offFrame();
      offJobs();
      offStart();
    };
  }, [refreshStatus]);

  // Request frames over the WebSocket while visible and running.
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => {
      if (document.visibilityState === "visible") events.send({ type: "browser.frame", quality: 55 });
    }, 1000 / fps);
    events.send({ type: "browser.frame", quality: 55 });
    return () => clearInterval(t);
  }, [running, fps]);

  const run = async (verb: string, args: unknown[] = []) => {
    setBusy(true);
    setError(null);
    try {
      const job = await api.post<Job>("/api/engine/command", { verb, args });
      if (job.status !== "done") setError(job.error?.message ?? job.status);
      await refreshStatus();
      events.send({ type: "browser.frame", quality: 55 });
      return job;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onImageClick = (e: React.MouseEvent<HTMLImageElement>) => {
    if (!frame || !img.current) return;
    const r = img.current.getBoundingClientRect();
    const scale = Math.min(r.width / frame.width, r.height / frame.height);
    const ox = (r.width - frame.width * scale) / 2;
    const oy = (r.height - frame.height * scale) / 2;
    const x = (e.clientX - r.left - ox) / scale;
    const y = (e.clientY - r.top - oy) / scale;
    if (x < 0 || y < 0 || x > frame.width || y > frame.height) return;
    run("click_at", [Math.round(x), Math.round(y)]);
  };

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="browser-view">
      <div className="flex items-center gap-1 border-b p-1.5" style={{ borderColor: "var(--panel-border)" }}>
        <button className="btn btn-sm btn-ghost" onClick={() => run("back")} aria-label="Назад" disabled={!running}>
          <ArrowLeft size={14} />
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => run("forward")} aria-label="Вперёд" disabled={!running}>
          <ArrowRight size={14} />
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => run("reload")} aria-label="Обновить" disabled={!running}>
          <RotateCw size={14} />
        </button>
        <form
          className="flex min-w-0 flex-1"
          onSubmit={(e) => {
            e.preventDefault();
            run("load", [address]);
          }}
        >
          <input
            className="input !rounded-xl !py-1 font-mono !text-xs"
            value={address}
            onFocus={() => (focused.current = true)}
            onBlur={() => (focused.current = false)}
            onChange={(e) => setAddress(e.target.value)}
            aria-label="Адрес"
          />
        </form>
        {busy && <Loader2 size={14} className="animate-spin" />}
        <button className="btn btn-sm" onClick={() => run("screenshot")} disabled={!running} title="Скриншот → MUR Drive/Screenshots">
          <Camera size={14} />
        </button>
        <button className={`btn btn-sm ${running ? "btn-danger" : "btn-primary"}`} onClick={() => run(running ? "stop" : "start")} title={running ? "Остановить браузер" : "Запустить браузер"}>
          <Power size={14} />
        </button>
      </div>
      {tabs.length > 0 && (
        <div className="flex items-center gap-1 overflow-x-auto border-b px-1.5 py-1" style={{ borderColor: "var(--panel-border)" }}>
          {tabs.map((t) => (
            <div key={t.index} className={`tab flex max-w-[200px] items-center gap-1 ${t.active ? "tab-active" : ""}`}>
              <button className="truncate" onClick={() => run("tab", ["switch", t.index])}>
                {t.title || t.url}
              </button>
              {tabs.length > 1 && (
                <button onClick={() => run("tab", ["close", t.index])} aria-label="Закрыть вкладку">
                  <X size={11} />
                </button>
              )}
            </div>
          ))}
          <button className="btn btn-sm btn-ghost" onClick={() => run("tab", ["new"])} aria-label="Новая вкладка">
            <Plus size={12} />
          </button>
        </div>
      )}
      <div
        className="relative min-h-0 flex-1 bg-black/50"
        tabIndex={0}
        onKeyDown={(e) => {
          if (!running || e.target !== e.currentTarget) return;
          if (e.key.length === 1) run("type", [e.key]);
          else if (["Enter", "Backspace", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
            e.preventDefault();
            run("press", [e.key]);
          }
        }}
        onWheel={(e) => running && run("scroll", [e.deltaY > 0 ? "down" : "up", Math.min(Math.abs(e.deltaY) * 2, 1200)])}
      >
        {running && frame ? (
          <img ref={img} src={frame.image} alt={frame.title} className="h-full w-full cursor-crosshair object-contain" onClick={onImageClick} draggable={false} />
        ) : (
          <div className="grid h-full place-items-center p-6 text-center text-sm muted">
            {running === false ? (
              <div>
                <div className="mb-2">Браузер Service Engine не запущен.</div>
                <button className="btn btn-primary" onClick={() => run("load", [address])}>
                  Открыть {address}
                </button>
                <div className="mt-3 text-xs">Это настоящий Chromium на сервере MURCLOUD (Playwright). Клики по изображению передаются в браузер.</div>
              </div>
            ) : (
              <Loader2 className="animate-spin" />
            )}
          </div>
        )}
      </div>
      {(error || log.length > 0) && (
        <div className="max-h-24 overflow-auto border-t p-1.5 font-mono text-[11px]" style={{ borderColor: "var(--panel-border)" }}>
          {error && <div className="text-rose-300">✖ {error}</div>}
          {log.slice(0, 6).map((j) => (
            <div key={j.id} className={j.status === "done" ? "muted" : "text-rose-300"}>
              [{j.source}] {j.command} → {j.status}
              {j.duration_ms != null && ` (${j.duration_ms} мс)`}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
