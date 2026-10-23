"""Turn script for the scripted test double: "build a site for Musya, run it and open it".

Used only by automated end-to-end tests (MUR_AI_ALLOW_SCRIPTED=1) to
exercise the real tool pipeline - files, preview server, Service Engine,
screenshots, approvals - without calling a paid model. Each step depends on
the *real* result of the previous tool call.
"""

from __future__ import annotations

import re

INDEX = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Сайт Муси</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <main class="card">
    <h1>🐈 Муся</h1>
    <p>Самая мурчащая кошка MURCLOUD.</p>
    <button id="mur">Погладить</button>
    <p id="count">Мур-счётчик: 0</p>
  </main>
  <script src="script.js"></script>
</body>
</html>
"""

STYLE = """body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui;
background:radial-gradient(circle at 30% 20%,#7c3aed,#0b1020 70%);color:#fff}
.card{padding:40px 56px;border-radius:20px;background:rgba(255,255,255,.08);backdrop-filter:blur(16px);
border:1px solid rgba(255,255,255,.18);box-shadow:0 0 40px #a855f766;text-align:center}
button{border:0;border-radius:20px;padding:12px 24px;font-size:18px;background:#22d3ee;color:#0b1020;cursor:pointer}
"""

SCRIPT = """let n = 0;
document.getElementById('mur').addEventListener('click', () => {
  n += 1;
  document.getElementById('count').textContent = 'Мур-счётчик: ' + n;
});
"""


def _results(state: dict) -> list[dict]:
    out = []
    for m in state["messages"]:
        if m.get("role") == "tool_results":
            out.extend(m["results"])
    return out


def demo_site_turns(state: dict) -> dict:
    step = sum(1 for m in state["messages"] if m.get("role") == "assistant")
    results = _results(state)
    plan = [{"title": t, "status": "pending"} for t in
            ["Создать файлы сайта", "Запустить локальный сервер", "Открыть сайт в браузере", "Проверить и показать"]]
    if step == 0:
        plan[0]["status"] = "in_progress"
        return {"text": "План готов, начинаю.", "tool_calls": [
            {"name": "update_plan", "input": {"steps": plan}},
            {"name": "write_file", "input": {"path": "site/index.html", "content": INDEX}},
            {"name": "write_file", "input": {"path": "site/style.css", "content": STYLE}},
            {"name": "write_file", "input": {"path": "site/script.js", "content": SCRIPT}},
        ]}
    if step == 1:
        if any(r["is_error"] for r in results[-3:]):
            return {"text": "Не удалось создать файлы: " + results[-1]["content"]}
        return {"text": "Файлы созданы. Запускаю веб-сервер.",
                "tool_calls": [{"name": "start_preview", "input": {"folder": "site"}}]}
    if step == 2:
        last = results[-1]
        m = re.search(r"browser load (http://127\.0\.0\.1:\d+/)", last["content"])
        if last["is_error"] or not m:
            return {"text": "Сервер не запустился: " + last["content"]}
        return {"text": "Открываю сайт.", "tool_calls": [{"name": "browser", "input": {"command": "load", "args": [m.group(1)]}}]}
    if step == 3:
        if results[-1]["is_error"]:
            return {"text": "Не удалось открыть сайт: " + results[-1]["content"]}
        return {"text": "Проверяю страницу.", "tool_calls": [
            {"name": "browser", "input": {"command": "click", "args": ["Погладить"]}},
            {"name": "browser", "input": {"command": "screenshot", "args": ["musya-site"]}},
            {"name": "browser", "input": {"command": "errors"}},
        ]}
    if step == 4:
        click, shot, errors = results[-3], results[-2], results[-1]
        ok = not click["is_error"] and not shot["is_error"] and '"console": []' in errors["content"]
        status = "done" if ok else "failed"
        for s in plan:
            s["status"] = status
        return {"text": ("Сайт Муси работает: файлы в site/, сервер запущен, кнопка нажимается, "
                         "скриншот сделан, ошибок в консоли нет. МУРГОТОВО!") if ok else
                        "Сайт открыт, но проверка нашла проблемы: " + errors["content"][:500],
                "tool_calls": [{"name": "update_plan", "input": {"steps": plan}}]}
    return {"text": ""}
