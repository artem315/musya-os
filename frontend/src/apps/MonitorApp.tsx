import { useEffect, useState } from "react";
import { api, formatBytes } from "../lib/api";

type Stats = {
  cpu_percent: number;
  cpu_count: number;
  load: number[] | null;
  memory: { total: number; used: number; percent: number };
  disk: { total: number; used: number; percent: number };
  process: { rss: number; threads: number };
  uptime: number;
  browser_sessions: number;
  my_browser: boolean;
  my_queue: number;
  ai_running: number;
  runs: number;
  previews: number;
  drive_used: number;
  drive_quota: number;
};

function Spark({ values, color }: { values: number[]; color: string }) {
  const w = 240;
  const h = 48;
  const pts = values.map((v, i) => `${(i / Math.max(values.length - 1, 1)) * w},${h - (Math.min(v, 100) / 100) * h}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-12 w-full" preserveAspectRatio="none" aria-hidden>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Gauge({ label, percent, detail, color }: { label: string; percent: number; detail: string; color: string }) {
  return (
    <div className="glass-soft rounded-2xl p-3">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-semibold">{label}</span>
        <span className="text-lg font-bold" style={{ color }}>
          {percent.toFixed(0)}%
        </span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/10">
        <div className="h-full rounded-full transition-all" style={{ width: `${percent}%`, background: color }} />
      </div>
      <div className="mt-1 text-xs muted">{detail}</div>
    </div>
  );
}

export default function MonitorApp() {
  const [s, setS] = useState<Stats | null>(null);
  const [cpu, setCpu] = useState<number[]>([]);
  const [mem, setMem] = useState<number[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await api.get<Stats>("/api/system/stats");
        if (!alive) return;
        setS(r);
        setCpu((c) => [...c, r.cpu_percent].slice(-60));
        setMem((m) => [...m, r.memory.percent].slice(-60));
        setErr(null);
      } catch (e) {
        setErr((e as Error).message);
      }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  if (!s) return <div className="grid h-full place-items-center muted">{err ?? "Сбор метрик сервера…"}</div>;
  const up = `${Math.floor(s.uptime / 3600)} ч ${Math.floor((s.uptime % 3600) / 60)} мин`;
  return (
    <div className="h-full overflow-auto p-3" data-testid="monitor">
      <div className="mb-2 text-xs muted">Реальные метрики сервера MURCLOUD (psutil), обновление каждые 2 с. Аптайм backend: {up}.</div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="glass-soft rounded-2xl p-3">
          <div className="flex justify-between text-sm font-semibold">
            CPU ({s.cpu_count} ядер) <span className="text-violet-300">{s.cpu_percent.toFixed(0)}%</span>
          </div>
          <Spark values={cpu} color="#a855f7" />
          {s.load && <div className="text-xs muted">Load: {s.load.map((l) => l.toFixed(2)).join(" / ")}</div>}
        </div>
        <div className="glass-soft rounded-2xl p-3">
          <div className="flex justify-between text-sm font-semibold">
            Память <span className="text-cyan-300">{s.memory.percent.toFixed(0)}%</span>
          </div>
          <Spark values={mem} color="#22d3ee" />
          <div className="text-xs muted">
            {formatBytes(s.memory.used)} / {formatBytes(s.memory.total)}
          </div>
        </div>
        <Gauge label="Диск данных" percent={s.disk.percent} detail={`${formatBytes(s.disk.used)} / ${formatBytes(s.disk.total)}`} color="#34d399" />
        <Gauge label="MUR Drive (ваш)" percent={(s.drive_used / s.drive_quota) * 100} detail={`${formatBytes(s.drive_used)} / ${formatBytes(s.drive_quota)}`} color="#f472b6" />
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
        {[
          ["Процесс backend", `${formatBytes(s.process.rss)} · ${s.process.threads} потоков`],
          ["Браузерных сессий", String(s.browser_sessions)],
          ["Ваш браузер", s.my_browser ? `запущен · очередь ${s.my_queue}` : "остановлен"],
          ["MUR AI задач", String(s.ai_running)],
          ["Сценариев", String(s.runs)],
          ["Preview-серверов", String(s.previews)],
        ].map(([k, v]) => (
          <div key={k} className="glass-soft rounded-2xl p-2.5">
            <div className="text-xs muted">{k}</div>
            <div className="font-semibold">{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
