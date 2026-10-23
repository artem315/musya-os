import { Gauge, Play, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three/webgpu";
import { api } from "../lib/api";

type Quality = "low" | "medium" | "high" | "ultra";
type ThemeKey = "murx" | "soup" | "classic";

const QUALITY: Record<Quality, { pixelRatio: number; shadow: number; orbs: number; cube: boolean; stars: number }> = {
  low: { pixelRatio: 0.75, shadow: 512, orbs: 6, cube: false, stars: 600 },
  medium: { pixelRatio: 1, shadow: 1024, orbs: 12, cube: false, stars: 1500 },
  high: { pixelRatio: Math.min(window.devicePixelRatio, 1.5), shadow: 2048, orbs: 24, cube: true, stars: 3000 },
  ultra: { pixelRatio: Math.min(window.devicePixelRatio, 2), shadow: 4096, orbs: 48, cube: true, stars: 6000 },
};

const THEMES: Record<ThemeKey, { name: string; note: string; colors: [string, string, string]; bg: string }> = {
  murx: { name: "MURX 10 000", note: "фантастическая тема — не реальная видеокарта", colors: ["#a855f7", "#22d3ee", "#f472b6"], bg: "#05051a" },
  soup: { name: "ULTRA GALAXY SOUP LSS", note: "фантастическая тема — не реальная технология", colors: ["#f97316", "#facc15", "#ec4899"], bg: "#120604" },
  classic: { name: "Classic Studio", note: "нейтральное освещение", colors: ["#ffffff", "#93c5fd", "#fde68a"], bg: "#0b0d12" },
};

function envTexture(colors: string[], bg: string) {
  const c = document.createElement("canvas");
  c.width = 1024;
  c.height = 512;
  const g = c.getContext("2d")!;
  const grad = g.createLinearGradient(0, 0, 0, 512);
  grad.addColorStop(0, bg);
  grad.addColorStop(0.5, "#1b1b3a");
  grad.addColorStop(1, bg);
  g.fillStyle = grad;
  g.fillRect(0, 0, 1024, 512);
  colors.forEach((col, i) => {
    g.fillStyle = col;
    g.globalAlpha = 0.9;
    g.fillRect(120 + i * 300, 150, 160, 40); // neon light panels reflected by metals
    g.fillRect(60 + i * 330, 330, 40, 120);
  });
  const t = new THREE.CanvasTexture(c);
  t.mapping = THREE.EquirectangularReflectionMapping;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

async function adapterInfo(renderer: THREE.WebGPURenderer): Promise<string> {
  const backend = renderer.backend as unknown as { isWebGPUBackend?: boolean; gl?: WebGL2RenderingContext };
  if (backend.isWebGPUBackend) {
    try {
      const gpu = (navigator as unknown as { gpu?: { requestAdapter(): Promise<{ info?: { vendor: string; architecture: string; description?: string } } | null> } }).gpu;
      const a = await gpu?.requestAdapter();
      const i = a?.info;
      return i ? `${i.vendor} ${i.architecture} ${i.description ?? ""}`.trim() || "WebGPU adapter" : "WebGPU adapter";
    } catch {
      return "WebGPU adapter";
    }
  }
  const gl = backend.gl;
  if (gl) {
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : String(gl.getParameter(gl.RENDERER));
  }
  return "unknown";
}

export default function RTXApp() {
  const host = useRef<HTMLDivElement>(null);
  const [quality, setQuality] = useState<Quality>("medium");
  const [theme, setTheme] = useState<ThemeKey>("murx");
  const [stats, setStats] = useState({ fps: 0, ms: 0, calls: 0, tris: 0 });
  const [info, setInfo] = useState<{ backend: string; adapter: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bench, setBench] = useState<{ running: boolean; progress: number; result?: { avg: number; low: number; score: number } }>({ running: false, progress: 0 });
  const [history, setHistory] = useState<{ ts: number; quality: string; theme: string; avg: number; low: number; score: number; backend: string }[]>([]);
  const benchRef = useRef<{ start: number; samples: number[] } | null>(null);

  useEffect(() => {
    api.get<{ value: { runs: typeof history } | null }>("/api/apps/rtx/state/benchmarks").then((r) => setHistory(r.value?.runs ?? [])).catch(() => undefined);
  }, []);

  useEffect(() => {
    const el = host.current!;
    const q = QUALITY[quality];
    const th = THEMES[theme];
    let disposed = false;
    let renderer: THREE.WebGPURenderer | null = null;
    const cleanups: (() => void)[] = [];

    (async () => {
      try {
        renderer = new THREE.WebGPURenderer({ antialias: quality !== "low" });
        await renderer.init();
      } catch (e) {
        setError(`Не удалось инициализировать WebGPU/WebGL: ${(e as Error).message}`);
        return;
      }
      if (disposed) {
        renderer.dispose();
        return;
      }
      const backend = (renderer.backend as unknown as { isWebGPUBackend?: boolean }).isWebGPUBackend ? "WebGPU" : "WebGL 2 (fallback)";
      setInfo({ backend, adapter: await adapterInfo(renderer) });
      renderer.setPixelRatio(q.pixelRatio);
      renderer.setSize(el.clientWidth, el.clientHeight);
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      el.appendChild(renderer.domElement);

      const scene = new THREE.Scene();
      scene.background = new THREE.Color(th.bg);
      scene.environment = envTexture(th.colors, th.bg);
      scene.fog = new THREE.Fog(th.bg, 18, 48);
      const camera = new THREE.PerspectiveCamera(55, el.clientWidth / el.clientHeight, 0.1, 200);
      camera.position.set(0, 4, 12);

      // Floor: polished metal reflects the environment and receives shadows.
      const floor = new THREE.Mesh(new THREE.CircleGeometry(30, 96), new THREE.MeshStandardMaterial({ color: "#1a1a2e", metalness: 0.9, roughness: 0.18 }));
      floor.rotation.x = -Math.PI / 2;
      floor.receiveShadow = true;
      scene.add(floor);

      // Hero object: torus knot core, or a soup bowl for the SOUP theme.
      let hero: THREE.Mesh;
      if (theme === "soup") {
        const pts = Array.from({ length: 20 }, (_, i) => new THREE.Vector2(Math.sin((i / 19) * Math.PI * 0.5) * 2.4 + 0.2, -Math.cos((i / 19) * Math.PI * 0.5) * 1.6 + 1.6));
        hero = new THREE.Mesh(new THREE.LatheGeometry(pts, 96), new THREE.MeshStandardMaterial({ color: "#f8fafc", metalness: 0.2, roughness: 0.25, side: THREE.DoubleSide }));
        const soup = new THREE.Mesh(new THREE.CircleGeometry(2.45, 64), new THREE.MeshStandardMaterial({ color: "#f97316", emissive: "#7c2d12", roughness: 0.4 }));
        soup.rotation.x = -Math.PI / 2;
        soup.position.y = 1.35;
        hero.add(soup);
        hero.position.y = 0.6;
      } else {
        hero = new THREE.Mesh(new THREE.TorusKnotGeometry(1.6, 0.5, quality === "low" ? 120 : 300, quality === "low" ? 16 : 48), new THREE.MeshStandardMaterial({ color: th.colors[0], metalness: 1, roughness: 0.08 }));
        hero.position.y = 3;
      }
      hero.castShadow = true;
      scene.add(hero);

      // Real-time reflections of the scene on a chrome sphere (high/ultra).
      let cubeCam: THREE.CubeCamera | null = null;
      const chrome = new THREE.Mesh(new THREE.SphereGeometry(1.2, 64, 32), new THREE.MeshStandardMaterial({ color: "#ffffff", metalness: 1, roughness: 0.02 }));
      chrome.position.set(-5, 1.2, 1);
      chrome.castShadow = true;
      scene.add(chrome);
      if (q.cube) {
        const rt = new THREE.CubeRenderTarget(256);
        cubeCam = new THREE.CubeCamera(0.1, 100, rt);
        (chrome.material as THREE.MeshStandardMaterial).envMap = rt.texture;
        scene.add(cubeCam);
      }

      const orbs: THREE.Mesh[] = [];
      for (let i = 0; i < q.orbs; i++) {
        const col = th.colors[i % 3];
        const m = new THREE.Mesh(new THREE.SphereGeometry(0.25 + (i % 4) * 0.08, 24, 16), new THREE.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 1.6, roughness: 0.3 }));
        m.castShadow = true;
        scene.add(m);
        orbs.push(m);
      }

      const sun = new THREE.DirectionalLight("#ffffff", 2.2);
      sun.position.set(6, 12, 6);
      sun.castShadow = true;
      sun.shadow.mapSize.set(q.shadow, q.shadow);
      sun.shadow.camera.left = sun.shadow.camera.bottom = -14;
      sun.shadow.camera.right = sun.shadow.camera.top = 14;
      scene.add(sun);
      scene.add(new THREE.HemisphereLight(th.colors[1], "#0b0b1a", 0.5));
      const lamps = th.colors.map((c, i) => {
        const l = new THREE.PointLight(c, 40, 20);
        l.position.set(Math.cos(i * 2.1) * 6, 2.5, Math.sin(i * 2.1) * 6);
        l.castShadow = quality === "ultra";
        scene.add(l);
        return l;
      });

      const starGeo = new THREE.BufferGeometry();
      const pos = new Float32Array(q.stars * 3);
      for (let i = 0; i < q.stars; i++) {
        const r = 40 + Math.random() * 40;
        const t = Math.random() * Math.PI * 2;
        const p = Math.acos(2 * Math.random() - 1);
        pos.set([r * Math.sin(p) * Math.cos(t), Math.abs(r * Math.cos(p)), r * Math.sin(p) * Math.sin(t)], i * 3);
      }
      starGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: "#ffffff", size: 0.15 })));

      const onResize = () => {
        if (!renderer) return;
        renderer.setSize(el.clientWidth, el.clientHeight);
        camera.aspect = el.clientWidth / Math.max(el.clientHeight, 1);
        camera.updateProjectionMatrix();
      };
      const ro = new ResizeObserver(onResize);
      ro.observe(el);
      cleanups.push(() => ro.disconnect());

      // Drag to orbit.
      let yaw = 0;
      let pitch = 0.3;
      let dragging = false;
      const down = () => (dragging = true);
      const up = () => (dragging = false);
      const move = (e: PointerEvent) => {
        if (!dragging) return;
        yaw -= e.movementX * 0.005;
        pitch = Math.min(1.2, Math.max(0.05, pitch + e.movementY * 0.004));
      };
      el.addEventListener("pointerdown", down);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointermove", move);
      cleanups.push(() => {
        el.removeEventListener("pointerdown", down);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointermove", move);
      });

      let frames = 0;
      let last = performance.now();
      let lastFrame = last;
      const clock = new THREE.Clock();
      renderer.setAnimationLoop(() => {
        const t = clock.getElapsedTime();
        const now = performance.now();
        const dt = now - lastFrame;
        lastFrame = now;
        const b = benchRef.current;
        if (b) {
          b.samples.push(1000 / Math.max(dt, 0.1));
          const k = (now - b.start) / 15000;
          yaw = k * Math.PI * 2;
          pitch = 0.25 + Math.sin(k * Math.PI * 2) * 0.2;
        } else if (!dragging) yaw += 0.002;
        const dist = 12;
        camera.position.set(Math.sin(yaw) * dist * Math.cos(pitch), 2 + Math.sin(pitch) * dist, Math.cos(yaw) * dist * Math.cos(pitch));
        camera.lookAt(0, 2, 0);
        hero.rotation.y = t * 0.4;
        if (theme !== "soup") hero.rotation.x = t * 0.25;
        orbs.forEach((o, i) => {
          const a = t * (0.3 + (i % 5) * 0.08) + i;
          const r = 4 + (i % 6) * 0.9;
          o.position.set(Math.cos(a) * r, 1 + Math.sin(a * 1.7 + i) * 1.4 + 1.5, Math.sin(a) * r);
        });
        lamps.forEach((l, i) => l.position.set(Math.cos(t * 0.5 + i * 2.1) * 6, 2.5 + Math.sin(t + i), Math.sin(t * 0.5 + i * 2.1) * 6));
        if (cubeCam) {
          chrome.visible = false;
          cubeCam.position.copy(chrome.position);
          cubeCam.update(renderer!, scene);
          chrome.visible = true;
        }
        renderer!.render(scene, camera);
        frames++;
        if (now - last >= 500) {
          const info = renderer!.info.render as unknown as { calls?: number; drawCalls?: number; triangles?: number };
          setStats({ fps: Math.round((frames * 1000) / (now - last)), ms: +(dt).toFixed(1), calls: info.drawCalls ?? info.calls ?? 0, tris: info.triangles ?? 0 });
          frames = 0;
          last = now;
        }
      });
      cleanups.push(() => {
        renderer?.setAnimationLoop(null);
        scene.traverse((o) => {
          const m = o as THREE.Mesh;
          m.geometry?.dispose();
          const mat = m.material as THREE.Material | THREE.Material[] | undefined;
          (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((x) => x.dispose());
        });
      });
    })();

    return () => {
      disposed = true;
      cleanups.forEach((c) => c());
      if (renderer) {
        renderer.dispose();
        renderer.domElement.remove();
      }
    };
  }, [quality, theme]);

  // Benchmark progress & result.
  useEffect(() => {
    if (!bench.running) return;
    const t = setInterval(() => {
      const b = benchRef.current;
      if (!b) return;
      const k = (performance.now() - b.start) / 15000;
      if (k >= 1) {
        benchRef.current = null;
        const s = b.samples.slice(30).sort((a, c) => a - c);
        const avg = s.reduce((a, c) => a + c, 0) / Math.max(s.length, 1);
        const low = s[Math.floor(s.length * 0.01)] ?? 0;
        const factor = { low: 0.5, medium: 1, high: 1.6, ultra: 2.4 }[quality];
        const score = Math.round(avg * factor * 100);
        const result = { avg: +avg.toFixed(1), low: +low.toFixed(1), score };
        setBench({ running: false, progress: 1, result });
        const runs = [{ ts: Date.now(), quality, theme: THEMES[theme].name, ...result, backend: info?.backend ?? "" }, ...history].slice(0, 20);
        setHistory(runs);
        api.put("/api/apps/rtx/state/benchmarks", { value: { runs } }).catch(() => undefined);
      } else setBench((x) => ({ ...x, progress: k }));
    }, 200);
    return () => clearInterval(t);
  }, [bench.running, quality, theme, info, history]);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="rtx">
      <div className="flex flex-wrap items-center gap-1.5 border-b p-1.5 text-xs" style={{ borderColor: "var(--panel-border)" }}>
        <span className="muted">Качество</span>
        {(Object.keys(QUALITY) as Quality[]).map((k) => (
          <button key={k} className={`tab ${quality === k ? "tab-active" : ""}`} onClick={() => setQuality(k)} disabled={bench.running}>
            {k}
          </button>
        ))}
        <span className="ml-2 muted">Тема</span>
        <select className="input !w-auto !py-1 !text-xs" value={theme} onChange={(e) => setTheme(e.target.value as ThemeKey)} disabled={bench.running}>
          {(Object.keys(THEMES) as ThemeKey[]).map((k) => (
            <option key={k} value={k}>
              {THEMES[k].name}
            </option>
          ))}
        </select>
        <button
          className={`btn btn-sm ${bench.running ? "btn-danger" : "btn-primary"} ml-auto`}
          onClick={() => {
            if (bench.running) {
              benchRef.current = null;
              setBench({ running: false, progress: 0 });
            } else {
              benchRef.current = { start: performance.now(), samples: [] };
              setBench({ running: true, progress: 0 });
            }
          }}
        >
          {bench.running ? <Square size={12} /> : <Play size={12} />} {bench.running ? "Остановить" : "Бенчмарк 15 с"}
        </button>
      </div>
      <div className="relative min-h-0 flex-1">
        <div ref={host} className="absolute inset-0 touch-none" />
        <div className="pointer-events-none absolute left-2 top-2 rounded-xl bg-black/55 px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-white">
          <div className="flex items-center gap-1 text-sm font-bold">
            <Gauge size={14} /> {stats.fps} FPS
          </div>
          <div>{stats.ms} ms · draw calls {stats.calls} · tris {stats.tris.toLocaleString()}</div>
          {info && (
            <>
              <div>Renderer: {info.backend}</div>
              <div className="max-w-[320px] truncate">GPU: {info.adapter}</div>
            </>
          )}
          <div className="text-amber-200">
            {THEMES[theme].name}: {THEMES[theme].note}
          </div>
        </div>
        {bench.running && (
          <div className="absolute bottom-3 left-1/2 w-64 -translate-x-1/2 rounded-full bg-black/60 p-1">
            <div className="h-2 rounded-full bg-gradient-to-r from-violet-500 to-cyan-400" style={{ width: `${bench.progress * 100}%` }} />
          </div>
        )}
        {(bench.result || history.length > 0) && !bench.running && (
          <div className="absolute bottom-3 right-3 max-h-[45%] w-72 overflow-auto rounded-2xl bg-black/60 p-2.5 text-xs text-white">
            {bench.result && (
              <div className="mb-2">
                <div className="text-sm font-bold">Счёт: {bench.result.score}</div>
                Среднее {bench.result.avg} FPS · 1% low {bench.result.low} FPS
              </div>
            )}
            <div className="mb-1 font-semibold">История</div>
            {history.map((h) => (
              <div key={h.ts} className="flex justify-between gap-2 opacity-80">
                <span>
                  {h.quality} · {h.backend.split(" ")[0]}
                </span>
                <span>
                  {h.score} ({h.avg} FPS)
                </span>
              </div>
            ))}
          </div>
        )}
        {error && <div className="absolute inset-0 grid place-items-center p-6 text-center text-sm text-rose-300">{error}</div>}
      </div>
    </div>
  );
}
