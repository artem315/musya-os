import { motion } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";
import { useGameInput, type Action, type Dir } from "./input";

export type GameProps = { onScore: (score: number) => void; best: number };

// ------------------------------------------------------------------ Мурзмейка (Snake)
export function Snake({ onScore, best }: GameProps) {
  const N = 18;
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [score, setScore] = useState(0);
  const [state, setState] = useState<"ready" | "play" | "over">("ready");
  const g = useRef({ snake: [[9, 9]] as number[][], dir: "right" as Dir, next: "right" as Dir, fish: [4, 4], score: 0 });

  const reset = () => {
    g.current = { snake: [[9, 9], [8, 9], [7, 9]], dir: "right", next: "right", fish: [13, 9], score: 0 };
    setScore(0);
    setState("play");
  };

  useGameInput(box, (a: Action) => {
    if (a === "start" || a === "a") {
      if (state !== "play") reset();
      return;
    }
    const opp: Record<Dir, Dir> = { up: "down", down: "up", left: "right", right: "left" };
    if (opp[a as Dir] !== g.current.dir) g.current.next = a as Dir;
  });

  useEffect(() => {
    if (state !== "play") return;
    const t = setInterval(() => {
      const s = g.current;
      s.dir = s.next;
      const [hx, hy] = s.snake[0];
      const d = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[s.dir];
      const head = [hx + d[0], hy + d[1]];
      if (head[0] < 0 || head[1] < 0 || head[0] >= N || head[1] >= N || s.snake.some(([x, y]) => x === head[0] && y === head[1])) {
        setState("over");
        onScore(s.score);
        return;
      }
      s.snake.unshift(head);
      if (head[0] === s.fish[0] && head[1] === s.fish[1]) {
        s.score += 10;
        setScore(s.score);
        do s.fish = [Math.floor(Math.random() * N), Math.floor(Math.random() * N)];
        while (s.snake.some(([x, y]) => x === s.fish[0] && y === s.fish[1]));
      } else s.snake.pop();
      const c = canvas.current?.getContext("2d");
      if (!c) return;
      const cell = c.canvas.width / N;
      c.fillStyle = "#0b0b1a";
      c.fillRect(0, 0, c.canvas.width, c.canvas.height);
      c.font = `${cell * 0.9}px serif`;
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillText("🐟", (s.fish[0] + 0.5) * cell, (s.fish[1] + 0.55) * cell);
      s.snake.forEach(([x, y], i) => {
        c.fillStyle = i === 0 ? "#f472b6" : `hsl(${280 - i * 4}, 80%, ${60 - Math.min(i, 20)}%)`;
        c.beginPath();
        c.roundRect(x * cell + 1, y * cell + 1, cell - 2, cell - 2, 6);
        c.fill();
      });
    }, Math.max(70, 140 - g.current.score / 2));
    return () => clearInterval(t);
  }, [state, onScore, score]);

  return (
    <div ref={box} tabIndex={0} className="flex flex-col items-center gap-2 outline-none" data-testid="game-snake">
      <div className="text-sm">
        Счёт {score} · рекорд {Math.max(best, score)}
      </div>
      <div className="relative">
        <canvas ref={canvas} width={360} height={360} className="max-w-full rounded-2xl border border-white/10 bg-[#0b0b1a]" />
        {state !== "play" && (
          <button className="absolute inset-0 grid place-items-center rounded-2xl bg-black/50 text-lg font-bold" onClick={reset}>
            {state === "over" ? `Игра окончена: ${score}. Ещё раз?` : "▶ Играть (Enter / A / тап)"}
          </button>
        )}
      </div>
      <div className="text-xs muted">Стрелки/WASD, свайпы или геймпад. Ешь рыбку, не кусай хвост.</div>
    </div>
  );
}

// ------------------------------------------------------------------ Мур 2048
type Grid = number[][];
const empty = (): Grid => Array.from({ length: 4 }, () => [0, 0, 0, 0]);
function spawn(g: Grid): Grid {
  const cells: [number, number][] = [];
  g.forEach((r, y) => r.forEach((v, x) => !v && cells.push([x, y])));
  if (!cells.length) return g;
  const [x, y] = cells[Math.floor(Math.random() * cells.length)];
  const n = g.map((r) => r.slice());
  n[y][x] = Math.random() < 0.9 ? 2 : 4;
  return n;
}
function slide(row: number[]): [number[], number] {
  const vals = row.filter(Boolean);
  let gained = 0;
  for (let i = 0; i < vals.length - 1; i++)
    if (vals[i] === vals[i + 1]) {
      vals[i] *= 2;
      gained += vals[i];
      vals.splice(i + 1, 1);
    }
  return [[...vals, 0, 0, 0, 0].slice(0, 4), gained];
}
export function move2048(g: Grid, dir: Dir): [Grid, number, boolean] {
  const rot = (m: Grid) => m[0].map((_, i) => m.map((r) => r[i]).reverse());
  let m = g.map((r) => r.slice());
  const turns = { left: 0, up: 3, right: 2, down: 1 }[dir];
  for (let i = 0; i < turns; i++) m = rot(m);
  let gained = 0;
  m = m.map((r) => {
    const [n, s] = slide(r);
    gained += s;
    return n;
  });
  for (let i = 0; i < (4 - turns) % 4; i++) m = rot(m);
  const moved = JSON.stringify(m) !== JSON.stringify(g);
  return [m, gained, moved];
}
const LABELS: Record<number, string> = { 2: "🐾", 4: "🐟", 8: "🥛", 16: "🧶", 32: "🐭", 64: "🐈", 128: "😼", 256: "🍲", 512: "⭐", 1024: "🌌", 2048: "👑" };

export function Game2048({ onScore, best }: GameProps) {
  const box = useRef<HTMLDivElement>(null);
  const [grid, setGrid] = useState<Grid>(() => spawn(spawn(empty())));
  const [score, setScore] = useState(0);
  const [over, setOver] = useState(false);
  const restart = () => (setGrid(spawn(spawn(empty()))), setScore(0), setOver(false));
  const act = useCallback(
    (a: Action) => {
      if (a === "start") return restart();
      if (over || a === "a") return;
      setGrid((g) => {
        const [n, gained, moved] = move2048(g, a as Dir);
        if (!moved) return g;
        const s = spawn(n);
        setScore((x) => x + gained);
        const stuck = (["up", "down", "left", "right"] as Dir[]).every((d) => !move2048(s, d)[2]);
        if (stuck) {
          setOver(true);
          setScore((x) => (onScore(x), x));
        }
        return s;
      });
    },
    [over, onScore],
  );
  useGameInput(box, act);
  return (
    <div ref={box} tabIndex={0} className="flex flex-col items-center gap-2 outline-none" data-testid="game-2048">
      <div className="text-sm">
        Счёт {score} · рекорд {Math.max(best, score)}{" "}
        <button className="btn btn-sm ml-2" onClick={() => (onScore(score), restart())}>
          Заново
        </button>
      </div>
      <div className="grid grid-cols-4 gap-2 rounded-2xl bg-white/5 p-2">
        {grid.flat().map((v, i) => (
          <motion.div
            key={i}
            layout
            className="grid h-16 w-16 place-items-center rounded-xl text-center text-xl font-bold sm:h-20 sm:w-20"
            style={{ background: v ? `hsl(${280 - Math.log2(v) * 18}, 70%, ${30 + Math.log2(v) * 3}%)` : "rgba(255,255,255,.05)" }}
          >
            {v ? (
              <span>
                {LABELS[v] ?? "💎"}
                <div className="text-[10px] font-normal opacity-80">{v}</div>
              </span>
            ) : null}
          </motion.div>
        ))}
      </div>
      {over && <div className="font-bold text-rose-300">Ходов нет! Счёт {score}</div>}
      <div className="text-xs muted">Соединяй одинаковые плитки: 🐾 → 🐟 → 🥛 … → 👑 2048</div>
    </div>
  );
}

// ------------------------------------------------------------------ Мур-мемори
const FACES = ["🐈", "🐟", "🧶", "🍲", "🐭", "⭐", "🥛", "😼"];
export function Memory({ onScore, best }: GameProps) {
  const deal = () => [...FACES, ...FACES].map((f, i) => ({ id: i, f, open: false, done: false })).sort(() => Math.random() - 0.5);
  const [cards, setCards] = useState(deal);
  const [moves, setMoves] = useState(0);
  const [lock, setLock] = useState(false);
  const flip = (id: number) => {
    if (lock) return;
    const c = cards.find((x) => x.id === id)!;
    if (c.open || c.done) return;
    const opened = cards.filter((x) => x.open && !x.done);
    const next = cards.map((x) => (x.id === id ? { ...x, open: true } : x));
    setCards(next);
    if (opened.length === 1) {
      setMoves((m) => m + 1);
      const other = opened[0];
      if (other.f === c.f) {
        const done = next.map((x) => (x.f === c.f ? { ...x, done: true } : x));
        setCards(done);
        if (done.every((x) => x.done)) onScore(Math.max(0, 200 - (moves + 1) * 5));
      } else {
        setLock(true);
        setTimeout(() => {
          setCards((cs) => cs.map((x) => (x.id === id || x.id === other.id ? { ...x, open: false } : x)));
          setLock(false);
        }, 700);
      }
    }
  };
  const won = cards.every((c) => c.done);
  return (
    <div className="flex flex-col items-center gap-2" data-testid="game-memory">
      <div className="text-sm">
        Ходов {moves} · рекорд {best}{" "}
        <button className="btn btn-sm ml-2" onClick={() => (setCards(deal()), setMoves(0))}>
          Заново
        </button>
      </div>
      <div className="grid grid-cols-4 gap-2">
        {cards.map((c) => (
          <motion.button
            key={c.id}
            className="grid h-16 w-16 place-items-center rounded-xl text-3xl sm:h-20 sm:w-20"
            style={{ background: c.open || c.done ? "rgba(168,85,247,.35)" : "linear-gradient(135deg,#a855f7,#22d3ee)" }}
            animate={{ rotateY: c.open || c.done ? 180 : 0 }}
            onClick={() => flip(c.id)}
          >
            <span style={{ transform: "rotateY(180deg)" }}>{c.open || c.done ? c.f : ""}</span>
          </motion.button>
        ))}
      </div>
      {won && <div className="font-bold text-emerald-300">Мур! Все пары найдены за {moves} ходов.</div>}
    </div>
  );
}
