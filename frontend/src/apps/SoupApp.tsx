import { AnimatePresence, motion } from "framer-motion";
import { Award, RotateCcw, Volume2, VolumeX } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

type Ingredient = { id: string; emoji: string; name: string };
const INGREDIENTS: Ingredient[] = [
  { id: "fish", emoji: "🐟", name: "Рыбка" },
  { id: "cream", emoji: "🥛", name: "Сливки" },
  { id: "carrot", emoji: "🥕", name: "Морковка" },
  { id: "mint", emoji: "🌿", name: "Кошачья мята" },
  { id: "star", emoji: "⭐", name: "Звёздная пыль" },
  { id: "cheese", emoji: "🧀", name: "Сыр" },
];

type Recipe = { id: string; name: string; needs: string[]; stir: number; unlock: number; reward: number };
const RECIPES: Recipe[] = [
  { id: "classic", name: "Классический мурсуп", needs: ["fish", "carrot", "cream"], stir: 3, unlock: 0, reward: 5 },
  { id: "creamy", name: "Сливочный мурсуп", needs: ["cream", "cream", "cheese", "fish"], stir: 4, unlock: 3, reward: 8 },
  { id: "mint", name: "Мятный мурсуп Красавчика", needs: ["mint", "fish", "carrot", "mint"], stir: 5, unlock: 7, reward: 12 },
  { id: "galaxy", name: "ULTRA GALAXY мурсуп", needs: ["star", "star", "fish", "cream", "mint"], stir: 7, unlock: 15, reward: 25 },
];

type Achievement = { id: string; name: string; desc: string };
const ACHIEVEMENTS: Achievement[] = [
  { id: "first", name: "Первая порция", desc: "Сварить первый мурсуп" },
  { id: "ten", name: "Мур-повар", desc: "10 порций" },
  { id: "fifty", name: "Шеф MURCLOUD", desc: "50 порций" },
  { id: "all", name: "Коллекционер рецептов", desc: "Сварить все рецепты" },
  { id: "fast", name: "Тыгыдык!", desc: "Сварить суп быстрее 8 секунд" },
  { id: "galaxy", name: "Галактический вкус", desc: "Сварить ULTRA GALAXY мурсуп" },
  { id: "oops", name: "Ой, не то", desc: "Испортить суп и начать заново" },
];

type Progress = { portions: number; coins: number; achievements: string[]; cooked: Record<string, number>; best: number | null };
const EMPTY: Progress = { portions: 0, coins: 0, achievements: [], cooked: {}, best: null };

// --- WebAudio sound effects (synthesised, no audio files) ---------------
let ctx: AudioContext | null = null;
function tone(freq: number, dur = 0.12, type: OscillatorType = "sine", gain = 0.12, when = 0) {
  ctx = ctx ?? new AudioContext();
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.type = type;
  o.frequency.setValueAtTime(freq, ctx.currentTime + when);
  o.frequency.exponentialRampToValueAtTime(Math.max(40, freq * 0.6), ctx.currentTime + when + dur);
  g.gain.setValueAtTime(gain, ctx.currentTime + when);
  g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + when + dur);
  o.connect(g).connect(ctx.destination);
  o.start(ctx.currentTime + when);
  o.stop(ctx.currentTime + when + dur + 0.02);
}
const sfx = {
  plop: () => tone(520, 0.15, "sine", 0.15),
  stir: () => tone(180 + Math.random() * 60, 0.08, "triangle", 0.05),
  win: () => [523, 659, 784, 1047].forEach((f, i) => tone(f, 0.18, "square", 0.06, i * 0.09)),
  fail: () => [300, 220].forEach((f, i) => tone(f, 0.22, "sawtooth", 0.05, i * 0.14)),
};

const LINES = {
  musya: ["Мур! Хочу супчик 🐈", "Тыгыдык к кастрюле!", "Пахнет рыбкой… мур-р-р", "Ещё порцию, пожалуйста!"],
  krasavchik: ["Красавчик: сначала ингредиенты по рецепту, потом мешаем!", "Красавчик: мешай по кругу — пальцем или мышкой.", "Красавчик: лишний ингредиент испортит суп."],
  artem: ["Артём: прогресс сохраняется на сервере MURCLOUD.", "Артём (ARTYONIX STUDIOS): новые рецепты открываются за порции.", "Артём: ачивки видно справа — собери все!"],
};

export default function SoupApp() {
  const [p, setP] = useState<Progress>(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const [recipe, setRecipe] = useState<Recipe>(RECIPES[0]);
  const [pot, setPot] = useState<string[]>([]);
  const [stirs, setStirs] = useState(0);
  const [drops, setDrops] = useState<{ key: number; emoji: string }[]>([]);
  const [talk, setTalk] = useState<string>(LINES.musya[0]);
  const [toast, setToast] = useState<string | null>(null);
  const [sound, setSound] = useState(true);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const stirAngle = useRef<number | null>(null);
  const stirAcc = useRef(0);

  useEffect(() => {
    api
      .get<{ value: Progress | null }>("/api/apps/soup/state/progress")
      .then((r) => setP({ ...EMPTY, ...(r.value ?? {}) }))
      .finally(() => setLoaded(true));
  }, []);
  useEffect(() => {
    if (!loaded) return;
    const t = setTimeout(() => api.put("/api/apps/soup/state/progress", { value: p }).catch(() => undefined), 600);
    return () => clearTimeout(t);
  }, [p, loaded]);

  const play = useCallback((k: keyof typeof sfx) => sound && sfx[k](), [sound]);

  const unlock = (prog: Progress, ids: string[]): Progress => {
    const fresh = ids.filter((id) => !prog.achievements.includes(id));
    if (fresh.length) {
      const a = ACHIEVEMENTS.find((x) => x.id === fresh[0])!;
      setToast(`🏆 ${a.name}`);
      setTimeout(() => setToast(null), 2500);
    }
    return { ...prog, achievements: [...prog.achievements, ...fresh] };
  };

  const add = (ing: Ingredient) => {
    if (pot.length >= 8) return;
    if (!startedAt) setStartedAt(Date.now());
    setPot((x) => [...x, ing.id]);
    setDrops((d) => [...d, { key: Date.now() + Math.random(), emoji: ing.emoji }]);
    play("plop");
    setTalk(LINES.musya[Math.floor(Math.random() * LINES.musya.length)]);
  };

  const matches = (() => {
    const need = [...recipe.needs].sort().join(",");
    return [...pot].sort().join(",") === need;
  })();
  const wrong = (() => {
    const counts: Record<string, number> = {};
    recipe.needs.forEach((n) => (counts[n] = (counts[n] ?? 0) + 1));
    const have: Record<string, number> = {};
    for (const x of pot) {
      have[x] = (have[x] ?? 0) + 1;
      if (have[x] > (counts[x] ?? 0)) return true;
    }
    return false;
  })();

  useEffect(() => {
    if (wrong) {
      setTalk("Красавчик: Ой! Этого нет в рецепте. Суп испорчен — начнём заново.");
      play("fail");
      const t = setTimeout(() => {
        setPot([]);
        setStirs(0);
        setStartedAt(null);
        setP((x) => unlock(x, ["oops"]));
      }, 1200);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wrong]);

  const onStir = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!matches || e.buttons === 0) return;
    const r = e.currentTarget.getBoundingClientRect();
    const a = Math.atan2(e.clientY - (r.top + r.height / 2), e.clientX - (r.left + r.width / 2));
    if (stirAngle.current !== null) {
      let d = a - stirAngle.current;
      if (d > Math.PI) d -= Math.PI * 2;
      if (d < -Math.PI) d += Math.PI * 2;
      stirAcc.current += Math.abs(d);
      if (stirAcc.current >= Math.PI * 2) {
        stirAcc.current = 0;
        play("stir");
        setStirs((s) => s + 1);
      }
    }
    stirAngle.current = a;
  };

  useEffect(() => {
    if (!matches || stirs < recipe.stir) return;
    const secs = startedAt ? (Date.now() - startedAt) / 1000 : 99;
    play("win");
    setTalk(`Готово! ${recipe.name} — +${recipe.reward} монет. Муся довольна 😻`);
    setP((x) => {
      const cooked = { ...x.cooked, [recipe.id]: (x.cooked[recipe.id] ?? 0) + 1 };
      let n: Progress = { ...x, portions: x.portions + 1, coins: x.coins + recipe.reward, cooked, best: x.best === null ? secs : Math.min(x.best, secs) };
      const ids = ["first"];
      if (n.portions >= 10) ids.push("ten");
      if (n.portions >= 50) ids.push("fifty");
      if (RECIPES.every((r) => cooked[r.id])) ids.push("all");
      if (secs < 8) ids.push("fast");
      if (recipe.id === "galaxy") ids.push("galaxy");
      n = unlock(n, ids);
      return n;
    });
    setPot([]);
    setStirs(0);
    setStartedAt(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stirs, matches]);

  return (
    <div className="flex h-full min-h-0 flex-col sm:flex-row" data-testid="soup">
      <div className="relative flex min-h-0 flex-1 flex-col items-center gap-3 overflow-auto p-4 [&>*]:shrink-0">
        <div className="flex w-full items-center gap-2 text-sm">
          <span className="chip text-orange-300">🍲 Порций: {p.portions}</span>
          <span className="chip text-yellow-300">🪙 {p.coins}</span>
          {p.best !== null && <span className="chip">⏱ рекорд {p.best.toFixed(1)} с</span>}
          <button className="btn btn-sm btn-ghost ml-auto" onClick={() => setSound((s) => !s)} aria-label="Звук">
            {sound ? <Volume2 size={14} /> : <VolumeX size={14} />}
          </button>
          <button className="btn btn-sm btn-ghost" onClick={() => (setPot([]), setStirs(0), setStartedAt(null))} aria-label="Начать заново">
            <RotateCcw size={14} />
          </button>
        </div>

        <div className="flex flex-wrap justify-center gap-1.5">
          {RECIPES.map((r) => {
            const locked = p.portions < r.unlock;
            return (
              <button key={r.id} className={`tab ${recipe.id === r.id ? "tab-active" : ""} ${locked ? "opacity-40" : ""}`} disabled={locked} onClick={() => (setRecipe(r), setPot([]), setStirs(0))}>
                {locked ? `🔒 ${r.unlock} порций` : r.name}
              </button>
            );
          })}
        </div>
        <div className="text-xs muted">
          Рецепт: {recipe.needs.map((n) => INGREDIENTS.find((i) => i.id === n)!.emoji).join(" ")} · перемешать {recipe.stir} раз(а)
        </div>

        <div className="relative mt-2 select-none" style={{ width: 260, height: 220 }}>
          <AnimatePresence>
            {drops.map((d) => (
              <motion.span
                key={d.key}
                className="absolute left-1/2 text-3xl"
                initial={{ y: -60, x: "-50%", opacity: 1, rotate: 0 }}
                animate={{ y: 80, opacity: 0, rotate: 200 }}
                transition={{ duration: 0.7, ease: "easeIn" }}
                onAnimationComplete={() => setDrops((x) => x.filter((y) => y.key !== d.key))}
              >
                {d.emoji}
              </motion.span>
            ))}
          </AnimatePresence>
          <motion.div
            className="absolute bottom-0 left-1/2 grid h-[170px] w-[240px] -translate-x-1/2 cursor-grab touch-none place-items-center rounded-b-[120px] rounded-t-[30px] border-4 border-zinc-300/70 bg-gradient-to-b from-zinc-500 to-zinc-800 shadow-2xl active:cursor-grabbing"
            onPointerMove={onStir}
            onPointerUp={() => (stirAngle.current = null)}
            onPointerLeave={() => (stirAngle.current = null)}
            animate={matches ? { rotate: [0, -2, 2, 0] } : {}}
            transition={{ repeat: Infinity, duration: 0.6 }}
            data-testid="pot"
          >
            <div className="absolute top-3 h-10 w-[200px] rounded-full" style={{ background: pot.length ? (recipe.id === "galaxy" ? "linear-gradient(90deg,#7c3aed,#ec4899,#22d3ee)" : "#fb923c") : "#3f3f46" }} />
            <div className="z-10 mt-6 text-center text-sm font-semibold text-white">
              {matches ? `Мешайте по кругу! ${stirs}/${recipe.stir}` : pot.length ? pot.map((x) => INGREDIENTS.find((i) => i.id === x)!.emoji).join("") : "Пустая кастрюля"}
            </div>
            {matches && (
              <motion.div className="absolute -top-6 text-2xl" animate={{ y: [-4, -16], opacity: [0.8, 0] }} transition={{ repeat: Infinity, duration: 1.2 }}>
                ♨️
              </motion.div>
            )}
          </motion.div>
        </div>

        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {INGREDIENTS.map((i) => (
            <motion.button key={i.id} whileTap={{ scale: 0.85 }} className="glass-soft flex flex-col items-center rounded-2xl px-3 py-2" onClick={() => add(i)} data-testid={`ing-${i.id}`}>
              <span className="text-3xl">{i.emoji}</span>
              <span className="text-[11px]">{i.name}</span>
            </motion.button>
          ))}
        </div>

        <div className="mt-1 flex w-full max-w-md items-start gap-2">
          <motion.div key={talk} initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="glass-soft flex-1 rounded-2xl p-2.5 text-sm">
            {talk}
          </motion.div>
        </div>
        <div className="flex gap-1.5">
          <button className="btn btn-sm" onClick={() => setTalk(LINES.krasavchik[Math.floor(Math.random() * LINES.krasavchik.length)])}>
            😼 Красавчик
          </button>
          <button className="btn btn-sm" onClick={() => setTalk(LINES.artem[Math.floor(Math.random() * LINES.artem.length)])}>
            👨‍💻 Артём
          </button>
        </div>
        <AnimatePresence>
          {toast && (
            <motion.div initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ opacity: 0 }} className="absolute bottom-4 rounded-2xl bg-amber-400 px-4 py-2 font-bold text-black shadow-xl">
              {toast}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <aside className="max-h-[35%] w-full shrink-0 overflow-auto border-t p-3 sm:max-h-none sm:w-60 sm:border-l sm:border-t-0" style={{ borderColor: "var(--panel-border)" }}>
        <div className="mb-2 flex items-center gap-1.5 font-semibold">
          <Award size={16} className="text-amber-300" /> Достижения {p.achievements.length}/{ACHIEVEMENTS.length}
        </div>
        <div className="space-y-1.5">
          {ACHIEVEMENTS.map((a) => {
            const got = p.achievements.includes(a.id);
            return (
              <div key={a.id} className={`rounded-xl p-2 text-xs ${got ? "bg-amber-400/15" : "opacity-50 glass-soft"}`}>
                <div className="font-semibold">
                  {got ? "🏆" : "🔒"} {a.name}
                </div>
                <div className="muted">{a.desc}</div>
              </div>
            );
          })}
        </div>
      </aside>
    </div>
  );
}
