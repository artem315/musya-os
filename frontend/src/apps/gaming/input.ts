import { useEffect, useRef } from "react";

export type Dir = "up" | "down" | "left" | "right";
export type Action = Dir | "a" | "start";

/** Keyboard (arrows/WASD/Enter/Space), touch swipes and Gamepad API, unified. */
export function useGameInput(target: React.RefObject<HTMLElement | null>, onAction: (a: Action) => void, enabled = true) {
  const cb = useRef(onAction);
  cb.current = onAction;

  useEffect(() => {
    if (!enabled) return;
    const keys: Record<string, Action> = {
      ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
      w: "up", s: "down", a: "left", d: "right", W: "up", S: "down", A: "left", D: "right",
      " ": "a", Enter: "start",
    };
    const onKey = (e: KeyboardEvent) => {
      const el = target.current;
      if (!el || !el.contains(document.activeElement)) return;
      const act = keys[e.key];
      if (act) {
        e.preventDefault();
        cb.current(act);
      }
    };
    window.addEventListener("keydown", onKey);

    let sx = 0;
    let sy = 0;
    const ts = (e: TouchEvent) => {
      sx = e.touches[0].clientX;
      sy = e.touches[0].clientY;
    };
    const te = (e: TouchEvent) => {
      const dx = e.changedTouches[0].clientX - sx;
      const dy = e.changedTouches[0].clientY - sy;
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 24) return cb.current("a");
      cb.current(Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : dy > 0 ? "down" : "up");
    };
    const el = target.current;
    el?.addEventListener("touchstart", ts, { passive: true });
    el?.addEventListener("touchend", te, { passive: true });

    // Gamepad polling (standard mapping: d-pad 12-15, A=0, Start=9, left stick axes 0/1).
    let raf = 0;
    const prev = new Map<string, boolean>();
    const poll = () => {
      const pads = navigator.getGamepads?.() ?? [];
      for (const gp of pads) {
        if (!gp) continue;
        const state: Record<Action, boolean> = {
          up: !!gp.buttons[12]?.pressed || gp.axes[1] < -0.6,
          down: !!gp.buttons[13]?.pressed || gp.axes[1] > 0.6,
          left: !!gp.buttons[14]?.pressed || gp.axes[0] < -0.6,
          right: !!gp.buttons[15]?.pressed || gp.axes[0] > 0.6,
          a: !!gp.buttons[0]?.pressed,
          start: !!gp.buttons[9]?.pressed,
        };
        for (const [k, v] of Object.entries(state)) {
          const key = `${gp.index}:${k}`;
          if (v && !prev.get(key)) cb.current(k as Action);
          prev.set(key, v);
        }
      }
      raf = requestAnimationFrame(poll);
    };
    raf = requestAnimationFrame(poll);

    return () => {
      window.removeEventListener("keydown", onKey);
      el?.removeEventListener("touchstart", ts);
      el?.removeEventListener("touchend", te);
      cancelAnimationFrame(raf);
    };
  }, [target, enabled]);
}

export function useGamepads() {
  const pads = useRef<string[]>([]);
  useEffect(() => {
    const on = () => (pads.current = (navigator.getGamepads?.() ?? []).filter(Boolean).map((g) => g!.id));
    window.addEventListener("gamepadconnected", on);
    window.addEventListener("gamepaddisconnected", on);
    on();
    return () => {
      window.removeEventListener("gamepadconnected", on);
      window.removeEventListener("gamepaddisconnected", on);
    };
  }, []);
  return pads;
}
