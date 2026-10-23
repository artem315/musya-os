import { motion } from "framer-motion";

export default function AboutApp() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <motion.img src="/murcloud.svg" alt="" className="h-24 w-24" animate={{ rotate: [0, 4, -4, 0] }} transition={{ repeat: Infinity, duration: 4 }} />
      <h2 className="text-2xl font-black">
        <span className="neon-text">MURCLOUD</span> 10 000 ULTRA
      </h2>
      <div className="text-sm">MURCLOUD OS · версия 10.0.0 Beta</div>
      <div className="text-sm muted">Powered by MUSYA Runtime &amp; Service Engine</div>
      <div className="text-sm font-semibold">Developed by ARTYONIX STUDIOS</div>
      <div className="mt-2 max-w-sm text-xs muted">
        Муся — пользователь, Красавчик — помощник, Артём — разработчик ARTYONIX STUDIOS. Браузер, ИИ, файлы и удалённый ПК работают на настоящем
        сервере; ничего не нарисовано.
      </div>
    </div>
  );
}
