import { Gamepad2, MonitorSmartphone, Trophy, User } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useOS } from "../../desktop/os";
import { api } from "../../lib/api";
import { Game2048, Memory, Snake, type GameProps } from "./games";
import { useGamepads } from "./input";

type Profile = { nickname: string; plays: Record<string, number>; best: Record<string, number>; history: { game: string; score: number; ts: number }[] };
const EMPTY: Profile = { nickname: "", plays: {}, best: {}, history: [] };

const LIBRARY: { id: string; name: string; emoji: string; desc: string; C: (p: GameProps) => React.ReactElement }[] = [
  { id: "snake", name: "Мурзмейка", emoji: "🐍", desc: "Классическая змейка: Муся ловит рыбок", C: Snake },
  { id: "2048", name: "Мур 2048", emoji: "🧩", desc: "Соединяй плитки до короны 👑", C: Game2048 },
  { id: "memory", name: "Мур-мемори", emoji: "🃏", desc: "Найди все пары котиков", C: Memory },
];

export default function GamingApp() {
  const os = useOS();
  const [tab, setTab] = useState<"library" | "profile" | "controller" | "pc">("library");
  const [game, setGame] = useState<string | null>(null);
  const [profile, setProfile] = useState<Profile>(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const pads = useGamepads();
  const [, force] = useState(0);

  useEffect(() => {
    api
      .get<{ value: Profile | null }>("/api/apps/gaming/state/profile")
      .then((r) => setProfile({ ...EMPTY, nickname: os.user.display_name, ...(r.value ?? {}) }))
      .finally(() => setLoaded(true));
  }, [os.user.display_name]);
  useEffect(() => {
    if (!loaded) return;
    const t = setTimeout(() => api.put("/api/apps/gaming/state/profile", { value: profile }).catch(() => undefined), 500);
    return () => clearTimeout(t);
  }, [profile, loaded]);
  useEffect(() => {
    const t = setInterval(() => force((x) => x + 1), 1500);
    return () => clearInterval(t);
  }, []);

  const onScore = useCallback(
    (id: string) => (score: number) =>
      setProfile((p) => ({
        ...p,
        plays: { ...p.plays, [id]: (p.plays[id] ?? 0) + 1 },
        best: { ...p.best, [id]: Math.max(p.best[id] ?? 0, score) },
        history: [{ game: id, score, ts: Date.now() }, ...p.history].slice(0, 30),
      })),
    [],
  );

  const current = LIBRARY.find((g) => g.id === game);
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="gaming">
      <div className="flex gap-1 border-b p-1.5" style={{ borderColor: "var(--panel-border)" }}>
        {(
          [
            ["library", "Библиотека", Gamepad2],
            ["profile", "Профиль", User],
            ["controller", "Контроллер", Gamepad2],
            ["pc", "MUR PC", MonitorSmartphone],
          ] as const
        ).map(([k, label, Icon]) => (
          <button key={k} className={`tab flex items-center gap-1 ${tab === k ? "tab-active" : ""}`} onClick={() => (setTab(k), setGame(null))}>
            <Icon size={13} /> {label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {tab === "library" && !current && (
          <div className="grid gap-3 sm:grid-cols-3">
            {LIBRARY.map((g) => (
              <button key={g.id} className="glass-soft rounded-mur p-4 text-left transition hover:scale-[1.02]" onClick={() => setGame(g.id)} data-testid={`play-${g.id}`}>
                <div className="text-4xl">{g.emoji}</div>
                <div className="mt-2 font-semibold">{g.name}</div>
                <div className="text-xs muted">{g.desc}</div>
                <div className="mt-2 text-xs">
                  🏆 {profile.best[g.id] ?? 0} · сыграно {profile.plays[g.id] ?? 0}
                </div>
              </button>
            ))}
            <div className="glass-soft rounded-mur p-4 text-xs muted sm:col-span-3">
              Здесь только игры, которые реально запускаются в браузере. Linux-игры (SGT Puzzles и др.) доступны внутри MUR PC.
            </div>
          </div>
        )}
        {tab === "library" && current && (
          <div>
            <button className="btn btn-sm mb-3" onClick={() => setGame(null)}>
              ← Библиотека
            </button>
            <current.C onScore={onScore(current.id)} best={profile.best[current.id] ?? 0} />
          </div>
        )}
        {tab === "profile" && (
          <div className="max-w-md space-y-3">
            <label className="block text-sm">
              Ник
              <input className="input mt-1" value={profile.nickname} onChange={(e) => setProfile({ ...profile, nickname: e.target.value.slice(0, 30) })} />
            </label>
            <div className="grid grid-cols-3 gap-2">
              {LIBRARY.map((g) => (
                <div key={g.id} className="glass-soft rounded-2xl p-2 text-center text-xs">
                  <div className="text-2xl">{g.emoji}</div>
                  <Trophy size={12} className="mx-auto text-amber-300" /> {profile.best[g.id] ?? 0}
                </div>
              ))}
            </div>
            <div className="text-sm font-semibold">Последние игры</div>
            {profile.history.map((h) => (
              <div key={h.ts} className="text-xs muted">
                {new Date(h.ts).toLocaleString("ru-RU")} — {LIBRARY.find((g) => g.id === h.game)?.name}: {h.score}
              </div>
            ))}
          </div>
        )}
        {tab === "controller" && (
          <div className="max-w-md space-y-2 text-sm">
            <div className="font-semibold">Подключённые геймпады (Gamepad API)</div>
            {pads.current.length === 0 ? <div className="muted">Не найдено. Подключите геймпад и нажмите любую кнопку.</div> : pads.current.map((p) => <div key={p} className="chip">{p}</div>)}
            <div className="mt-3 font-semibold">Управление</div>
            <ul className="list-disc space-y-1 pl-5 text-xs muted">
              <li>Клавиатура: стрелки / WASD, Enter — старт, Space — действие</li>
              <li>Сенсорный экран: свайпы, тап — действие</li>
              <li>Геймпад: крестовина или левый стик, A — действие, Start — старт</li>
            </ul>
          </div>
        )}
        {tab === "pc" && (
          <div className="max-w-md space-y-2 text-sm">
            <div className="font-semibold">Игры в MUR PC</div>
            <p className="muted">MUR PC — настоящий Linux-рабочий стол в облаке. В образе установлен набор головоломок SGT Puzzles. Запуск: MUR PC → «Игры».</p>
            <button className="btn btn-primary" onClick={() => os.open("pc")}>
              Открыть MUR PC
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
