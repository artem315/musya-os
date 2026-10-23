import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import {
  Bot,
  Code2,
  Cpu,
  Gamepad2,
  Globe,
  HardDrive,
  Info,
  MonitorSmartphone,
  Package,
  Settings,
  Soup,
  Sparkles,
  SquareTerminal,
  type LucideIcon,
} from "lucide-react";
import type { Win } from "../desktop/os";

export type AppProps = { win: Win };

export type AppDef = {
  title: string;
  icon: LucideIcon;
  color: string;
  size: [number, number];
  singleton?: boolean;
  component: LazyExoticComponent<ComponentType<AppProps>>;
  description: string;
};

export const APPS = {
  studio: {
    title: "MUR Service Studio",
    icon: Code2,
    color: "#a855f7",
    size: [1280, 800],
    component: lazy(() => import("./studio/Studio")),
    description: "IDE: редактор .mysya, терминал Service Engine, MUR AI, браузер, превью",
  },
  ai: {
    title: "MUR AI",
    icon: Bot,
    color: "#22d3ee",
    size: [520, 720],
    component: lazy(() => import("./AIApp")),
    description: "ИИ-агент, подключённый к Service Engine",
  },
  browser: {
    title: "MUR Browser",
    icon: Globe,
    color: "#38bdf8",
    size: [1100, 760],
    singleton: true,
    component: lazy(() => import("./BrowserApp")),
    description: "Управляемый Chromium на сервере (Playwright)",
  },
  drive: {
    title: "MUR Drive",
    icon: HardDrive,
    color: "#34d399",
    size: [900, 600],
    component: lazy(() => import("./DriveApp")),
    description: "Файлы: загрузка, папки, поиск, предпросмотр",
  },
  terminal: {
    title: "MUR Terminal",
    icon: SquareTerminal,
    color: "#e2e8f0",
    size: [820, 520],
    component: lazy(() => import("./TerminalApp")),
    description: "MUSYA REPL и команды service",
  },
  pc: {
    title: "MUR PC",
    icon: MonitorSmartphone,
    color: "#f472b6",
    size: [1200, 820],
    singleton: true,
    component: lazy(() => import("./PCApp")),
    description: "Удалённый Linux-рабочий стол (Docker + noVNC)",
  },
  gaming: {
    title: "MUR Gaming",
    icon: Gamepad2,
    color: "#fb923c",
    size: [960, 680],
    singleton: true,
    component: lazy(() => import("./gaming/GamingApp")),
    description: "Мини-игры, профиль, контроллер",
  },
  rtx: {
    title: "MUR RTX Lab",
    icon: Sparkles,
    color: "#facc15",
    size: [1100, 720],
    singleton: true,
    component: lazy(() => import("./RTXApp")),
    description: "WebGPU/WebGL 3D, тени, отражения, бенчмарк",
  },
  soup: {
    title: "MUR Soup",
    icon: Soup,
    color: "#f97316",
    size: [820, 640],
    singleton: true,
    component: lazy(() => import("./SoupApp")),
    description: "Приготовь мурсуп с Мусей",
  },
  monitor: {
    title: "System Monitor",
    icon: Cpu,
    color: "#4ade80",
    size: [720, 560],
    singleton: true,
    component: lazy(() => import("./MonitorApp")),
    description: "CPU, память, диск, сессии",
  },
  packages: {
    title: "MUR Package Manager",
    icon: Package,
    color: "#c084fc",
    size: [880, 620],
    singleton: true,
    component: lazy(() => import("./PackagesApp")),
    description: "Драйверы и пакеты с проверкой разрешений",
  },
  settings: {
    title: "MUR Settings",
    icon: Settings,
    color: "#94a3b8",
    size: [860, 640],
    singleton: true,
    component: lazy(() => import("./SettingsApp")),
    description: "Темы, ИИ-ключи, сессии, аудит",
  },
  about: {
    title: "О MURCLOUD",
    icon: Info,
    color: "#a78bfa",
    size: [520, 520],
    singleton: true,
    component: lazy(() => import("./AboutApp")),
    description: "MURCLOUD 10 000 ULTRA · ARTYONIX STUDIOS",
  },
} satisfies Record<string, AppDef>;

export type AppId = keyof typeof APPS;
export const APP_IDS = Object.keys(APPS) as AppId[];
