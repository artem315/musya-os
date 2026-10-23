"""Implementations of every ``service`` verb.

Each handler receives a :class:`CommandContext` and the evaluated argument
list, validates the arguments, performs the real Playwright / HTTP action and
returns JSON-compatible data. Handlers never pretend: if something cannot be
done they raise :class:`CommandError` with a human-readable reason.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from ..browser import BrowserSession, sanitize_filename, unique_path
from ..permissions.network import BlockedURL


class CommandError(Exception):
    def __init__(self, message: str, kind: str = "ServiceError"):
        super().__init__(message)
        self.kind = kind


@dataclass
class CommandSpec:
    verb: str
    handler: Callable[["CommandContext", list], Awaitable[Any]]
    min_args: int
    max_args: int
    timeout: float
    needs_browser: bool
    usage: str
    summary: str


@dataclass
class CommandContext:
    engine: Any  # ServiceEngine
    owner: str
    job: Any  # Job
    session: BrowserSession | None

    @property
    def page(self):
        if self.session is None:
            raise CommandError("browser is not started - run 'service start' or 'service load <url>'")
        try:
            return self.session.page
        except RuntimeError:
            raise CommandError("no open tabs - use 'service tab new'") from None

    def emit(self, type_: str, **data) -> None:
        self.engine.bus.publish(self.owner, type_, job=self.job.id if self.job else None, **data)


REGISTRY: dict[str, CommandSpec] = {}


def command(verb: str, min_args: int = 0, max_args: int | None = None, timeout: float = 20.0,
            needs_browser: bool = True, usage: str = "", summary: str = ""):
    def deco(fn):
        REGISTRY[verb] = CommandSpec(verb, fn, min_args, min_args if max_args is None else max_args, timeout,
                                     needs_browser, usage or f"service {verb}", summary)
        return fn
    return deco


# ----------------------------------------------------------------- helpers

def _text_arg(args: list, i: int, name: str, max_len: int = 2000) -> str:
    v = args[i]
    if isinstance(v, bool) or v is None:
        raise CommandError(f"{name} must be text")
    v = str(v) if isinstance(v, (int, float)) else v
    if not isinstance(v, str) or not v.strip():
        raise CommandError(f"{name} must be non-empty text")
    if len(v) > max_len:
        raise CommandError(f"{name} is too long (max {max_len})")
    return v


def _num_arg(args: list, i: int, name: str, default: float, lo: float, hi: float) -> float:
    if i >= len(args) or args[i] is None:
        return default
    v = args[i]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        try:
            v = float(str(v))
        except ValueError:
            raise CommandError(f"{name} must be a number") from None
    if not lo <= v <= hi:
        raise CommandError(f"{name} must be between {lo:g} and {hi:g}")
    return v


_SELECTOR_HINT = re.compile(r"^(css=|xpath=|text=|role=|#|\.[A-Za-z_-]|\[|//|[a-z]+\[)")


def is_selector(target: str) -> bool:
    return bool(_SELECTOR_HINT.match(target.strip()))


async def resolve_target(page, target: str, *, clickable: bool = False):
    """Find the element for ``target``: CSS/XPath selector or visible text."""
    t = target.strip()
    if is_selector(t):
        sel = t[4:] if t.startswith("css=") else t
        loc = page.locator(sel)
        if await loc.count() == 0:
            raise CommandError(f"no element matches selector '{t}'", "NotFound")
        return loc.first, f"selector {t}"
    candidates = []
    if clickable:
        for role in ("button", "link", "menuitem", "tab", "checkbox", "option"):
            candidates.append(page.get_by_role(role, name=t))
    candidates.append(page.get_by_text(t, exact=True))
    candidates.append(page.get_by_text(t))
    candidates.append(page.get_by_label(t))
    candidates.append(page.get_by_placeholder(t))
    candidates.append(page.get_by_title(t))
    for loc in candidates:
        count = await loc.count()
        for i in range(min(count, 10)):
            el = loc.nth(i)
            if await el.is_visible():
                return el, f"text '{t}'"
    raise CommandError(f"no visible element with text '{t}'", "NotFound")


async def page_info(page) -> dict:
    try:
        title = await page.title()
    except Exception:
        title = ""
    return {"url": page.url, "title": title}


# --------------------------------------------------------------- lifecycle

@command("start", needs_browser=False, timeout=60, summary="Start the browser session")
async def cmd_start(ctx: CommandContext, args: list):
    session = await ctx.engine.ensure_session(ctx.owner)
    return {"running": True, "tabs": len(session.pages), "browser": ctx.engine.pool.version}


@command("stop", needs_browser=False, summary="Close the browser session")
async def cmd_stop(ctx: CommandContext, args: list):
    closed = await ctx.engine.close_session(ctx.owner)
    return {"running": False, "closed": closed}


@command("status", needs_browser=False, summary="Show browser, tabs and queue state")
async def cmd_status(ctx: CommandContext, args: list):
    return await ctx.engine.status(ctx.owner)


# -------------------------------------------------------------- navigation

@command("load", 1, timeout=45, usage='service load "https://example.com"', summary="Open a URL")
async def cmd_load(ctx: CommandContext, args: list):
    raw = _text_arg(args, 0, "URL", 8192)
    try:
        url = await ctx.session.policy.check_url(raw)
    except BlockedURL as exc:
        raise CommandError(exc.reason, "BlockedURL") from None
    page = ctx.page
    try:
        response = await page.goto(url, wait_until="load", timeout=ctx.job.timeout * 1000)
    except Exception as exc:
        msg = str(exc).split("\n", 1)[0]
        if "ERR_BLOCKED_BY_CLIENT" in msg:
            blocked = ctx.session.blocked_requests[-1]["reason"] if ctx.session.blocked_requests else "policy"
            raise CommandError(f"navigation blocked: {blocked}", "BlockedURL") from None
        raise CommandError(f"could not load {url}: {msg}", "NavigationError") from None
    info = await page_info(page)
    info["status"] = response.status if response else None
    return info


@command("open", 1, timeout=45, summary="Alias of load")
async def cmd_open(ctx, args):
    return await cmd_load(ctx, args)


async def _history(ctx: CommandContext, action: str):
    page = ctx.page
    fn = {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[action]
    try:
        await fn(wait_until="load", timeout=ctx.job.timeout * 1000)
    except Exception as exc:
        raise CommandError(f"{action} failed: {str(exc).splitlines()[0]}", "NavigationError") from None
    return await page_info(page)


@command("back", timeout=30, summary="Go back in history")
async def cmd_back(ctx, args):
    return await _history(ctx, "back")


@command("forward", timeout=30, summary="Go forward in history")
async def cmd_forward(ctx, args):
    return await _history(ctx, "forward")


@command("reload", timeout=30, summary="Reload the page")
async def cmd_reload(ctx, args):
    return await _history(ctx, "reload")


@command("tab", 1, 2, timeout=30, usage="service tab new|list|switch N|close [N]", summary="Manage tabs")
async def cmd_tab(ctx: CommandContext, args: list):
    action = str(args[0]).lower()
    s = ctx.session
    if action == "list":
        return [{"index": i, "url": p.url, "title": await p.title(), "active": i == s.active}
                for i, p in enumerate(s.pages)]
    if action == "new":
        try:
            page = await s.new_page()
        except RuntimeError as exc:
            raise CommandError(str(exc)) from None
        s.active = s.index_of(page)
        if len(args) > 1:
            ctx.job.args = [args[1]]
            return await cmd_load(ctx, [args[1]])
        return {"index": s.active, "url": page.url}
    if action in ("switch", "close"):
        idx = int(_num_arg(args, 1, "tab index", s.active, 0, max(len(s.pages) - 1, 0)))
        if not s.pages:
            raise CommandError("no tabs open")
        if action == "switch":
            s.active = idx
            await s.pages[idx].bring_to_front()
            return {"index": idx, **await page_info(s.pages[idx])}
        await s.pages[idx].close()
        return {"closed": idx, "tabs": len(s.pages)}
    raise CommandError("tab action must be new, list, switch or close")


# --------------------------------------------------------------- reading

@command("search", 1, timeout=20, usage='service search "Download"', summary="Find elements by text")
async def cmd_search(ctx: CommandContext, args: list):
    query = _text_arg(args, 0, "search text", 500)
    page = ctx.page
    loc = page.get_by_text(query)
    total = await loc.count()
    matches = []
    for i in range(min(total, 25)):
        el = loc.nth(i)
        try:
            data = await el.evaluate(
                """e => ({tag: e.tagName.toLowerCase(), text: (e.innerText || e.textContent || '').trim().slice(0, 200),
                          href: e.closest('a') ? e.closest('a').href : null,
                          role: e.getAttribute('role'), id: e.id || null})""")
            box = await el.bounding_box()
            data["visible"] = await el.is_visible()
            data["box"] = {k: round(v) for k, v in box.items()} if box else None
            matches.append(data)
        except Exception:
            continue
    return {"query": query, "count": total, "matches": matches, "url": page.url}


@command("exists", 1, timeout=10, summary="true if visible text/selector (or the page title) matches")
async def cmd_exists(ctx: CommandContext, args: list):
    target = _text_arg(args, 0, "text", 500)
    return await _find_text(ctx.page, target) is not None


@command("text", 0, 1, timeout=15, summary="Visible text of the page or an element")
async def cmd_text(ctx: CommandContext, args: list):
    page = ctx.page
    if args:
        el, _ = await resolve_target(page, _text_arg(args, 0, "selector", 500))
        text = await el.inner_text()
    else:
        text = await page.locator("body").inner_text()
    return text[:20_000]


@command("url", summary="Current URL")
async def cmd_url(ctx, args):
    return ctx.page.url


@command("title", summary="Current page title")
async def cmd_title(ctx, args):
    return await ctx.page.title()


@command("html", summary="HTML of the current page (truncated to 200 KB)")
async def cmd_html(ctx, args):
    return (await ctx.page.content())[:200_000]


@command("errors", 0, 1, needs_browser=False, summary="Console errors and blocked requests")
async def cmd_errors(ctx: CommandContext, args: list):
    s = ctx.engine.pool.sessions.get(ctx.owner)
    if s is None:
        return {"console": [], "blocked": []}
    if args and str(args[0]).lower() == "clear":
        s.console_errors.clear()
        s.blocked_requests.clear()
    return {"console": list(s.console_errors[-50:]), "blocked": list(s.blocked_requests[-50:])}


@command("wait", 1, 2, timeout=125, usage='service wait "Download" [timeout_ms]',
         summary="Wait until text appears (or wait N ms)")
async def cmd_wait(ctx: CommandContext, args: list):
    started = time.monotonic()
    first = args[0]
    if isinstance(first, (int, float)) and not isinstance(first, bool):
        ms = _num_arg(args, 0, "milliseconds", 0, 0, 120_000)
        await asyncio.sleep(ms / 1000)
        return {"waited_ms": int(ms)}
    target = _text_arg(args, 0, "text", 500)
    timeout_ms = _num_arg(args, 1, "timeout", 10_000, 100, 120_000)
    page = ctx.page
    deadline = started + timeout_ms / 1000
    while True:
        where = await _find_text(page, target)
        if where:
            return {"found": True, "text": target, "where": where,
                    "waited_ms": int((time.monotonic() - started) * 1000)}
        if time.monotonic() >= deadline:
            raise CommandError(f"'{target}' did not appear within {int(timeout_ms)} ms", "Timeout")
        await asyncio.sleep(0.2)


async def _find_text(page, target: str) -> str | None:
    """Where ``target`` is currently visible: an element, or the page title."""
    try:
        await resolve_target(page, target)
        return "page"
    except CommandError:
        pass
    except Exception:
        return None  # page is navigating; try again
    try:
        if not is_selector(target) and target.lower() in (await page.title()).lower():
            return "title"
    except Exception:
        pass
    return None


# --------------------------------------------------------------- actions

@command("click", 1, timeout=20, usage='service click "Download"', summary="Click an element by text or selector")
async def cmd_click(ctx: CommandContext, args: list):
    target = _text_arg(args, 0, "click target", 500)
    page = ctx.page
    el, desc = await resolve_target(page, target, clickable=True)
    try:
        await el.scroll_into_view_if_needed(timeout=5000)
        await el.click(timeout=10_000)
    except Exception as exc:
        raise CommandError(f"could not click {desc}: {str(exc).splitlines()[0]}", "ActionError") from None
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    return {"clicked": desc, **await page_info(page)}


@command("dblclick", 1, timeout=20, summary="Double-click an element")
async def cmd_dblclick(ctx, args):
    el, desc = await resolve_target(ctx.page, _text_arg(args, 0, "target", 500), clickable=True)
    await el.dblclick(timeout=10_000)
    return {"double_clicked": desc}


@command("hover", 1, timeout=20, summary="Move the mouse over an element")
async def cmd_hover(ctx, args):
    el, desc = await resolve_target(ctx.page, _text_arg(args, 0, "target", 500), clickable=True)
    await el.hover(timeout=10_000)
    return {"hovered": desc}


@command("click_at", 2, timeout=10, usage="service click_at X Y", summary="Click at viewport coordinates")
async def cmd_click_at(ctx, args):
    vp = ctx.page.viewport_size or {"width": 1280, "height": 800}
    x = _num_arg(args, 0, "x", 0, 0, vp["width"])
    y = _num_arg(args, 1, "y", 0, 0, vp["height"])
    await ctx.page.mouse.click(x, y)
    return {"x": x, "y": y, **await page_info(ctx.page)}


@command("type", 1, 2, timeout=30, usage='service type "Hello" ["selector or label"]', summary="Type text")
async def cmd_type(ctx: CommandContext, args: list):
    text = args[0] if isinstance(args[0], str) else str(args[0])
    if len(text) > 10_000:
        raise CommandError("text is too long (max 10000 characters)")
    page = ctx.page
    if len(args) > 1 and args[1]:
        el, desc = await resolve_target(page, _text_arg(args, 1, "field", 500))
        await el.click(timeout=10_000)
        await page.keyboard.type(text, delay=5)
        return {"typed": len(text), "into": desc}
    focused = await page.evaluate(
        "() => { const e = document.activeElement; return !!e && e !== document.body && "
        "(e.isContentEditable || ['INPUT','TEXTAREA','SELECT'].includes(e.tagName)); }")
    into = "focused element"
    if not focused:
        field = page.locator("input:not([type=hidden]):not([disabled]), textarea, [contenteditable=true]")
        count = await field.count()
        target = None
        for i in range(min(count, 20)):
            if await field.nth(i).is_visible():
                target = field.nth(i)
                break
        if target is None:
            raise CommandError("no text field to type into - click a field first or pass one", "NotFound")
        await target.click(timeout=10_000)
        into = "first visible text field"
    await page.keyboard.type(text, delay=5)
    return {"typed": len(text), "into": into}


@command("fill", 2, timeout=20, usage='service fill "selector" "value"', summary="Set a field's value")
async def cmd_fill(ctx, args):
    el, desc = await resolve_target(ctx.page, _text_arg(args, 0, "field", 500))
    await el.fill(str(args[1])[:10_000], timeout=10_000)
    return {"filled": desc}


@command("select", 2, timeout=20, summary="Choose an option in a <select>")
async def cmd_select(ctx, args):
    el, desc = await resolve_target(ctx.page, _text_arg(args, 0, "select", 500))
    chosen = await el.select_option(str(args[1]), timeout=10_000)
    return {"selected": chosen, "in": desc}


_KEY_RE = re.compile(r"^((Control|Shift|Alt|Meta)\+)*([A-Za-z0-9]|F[1-9]|F1[0-2]|Enter|Tab|Escape|Backspace|"
                     r"Delete|Space|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|Home|End|PageUp|PageDown|Insert)$")


@command("press", 1, timeout=10, usage="service press Enter", summary="Press a key")
async def cmd_press(ctx: CommandContext, args: list):
    key = _text_arg(args, 0, "key", 64).strip()
    key = {"enter": "Enter", "tab": "Tab", "esc": "Escape", "escape": "Escape", "space": "Space",
           "backspace": "Backspace", "delete": "Delete", "up": "ArrowUp", "down": "ArrowDown",
           "left": "ArrowLeft", "right": "ArrowRight"}.get(key.lower(), key)
    if not _KEY_RE.match(key):
        raise CommandError(f"unsupported key '{key}'")
    await ctx.page.keyboard.press(" " if key == "Space" else key)
    try:
        await ctx.page.wait_for_load_state("domcontentloaded", timeout=3000)
    except Exception:
        pass
    return {"pressed": key, **await page_info(ctx.page)}


@command("scroll", 1, 2, timeout=10, usage="service scroll down 500", summary="Scroll the page")
async def cmd_scroll(ctx: CommandContext, args: list):
    direction = str(args[0]).lower()
    amount = _num_arg(args, 1, "amount", 400, 0, 100_000)
    dx, dy = {"down": (0, amount), "up": (0, -amount), "right": (amount, 0), "left": (-amount, 0)}.get(
        direction, (None, None))
    if direction in ("top", "bottom"):
        await ctx.page.evaluate("b => window.scrollTo(0, b ? document.body.scrollHeight : 0)", direction == "bottom")
    elif dx is None:
        raise CommandError("direction must be up, down, left, right, top or bottom")
    else:
        await ctx.page.mouse.wheel(dx, dy)
        await asyncio.sleep(0.15)
    pos = await ctx.page.evaluate("() => ({x: Math.round(window.scrollX), y: Math.round(window.scrollY)})")
    return {"direction": direction, "amount": amount, "position": pos}


# --------------------------------------------------------------- capture

@command("screenshot", 0, 1, timeout=30, usage="service screenshot [name]", summary="Capture the visible page")
async def cmd_screenshot(ctx: CommandContext, args: list):
    page = ctx.page
    name = sanitize_filename(str(args[0]) if args and args[0] else "", "")
    if not name:
        name = time.strftime("shot-%Y%m%d-%H%M%S") + f"-{ctx.job.id[:6]}"
    if not name.lower().endswith(".png"):
        name += ".png"
    target = unique_path(ctx.session.screenshots_dir, name)
    data = await page.screenshot(type="png", full_page=False)
    target.write_bytes(data)
    vp = page.viewport_size or {}
    info = {"filename": target.name, "path": str(target), "bytes": len(data), "width": vp.get("width"),
            "height": vp.get("height"), **await page_info(page)}
    ctx.emit("browser.screenshot", **{k: v for k, v in info.items() if k != "path"})
    return info


async def capture_frame(session: BrowserSession, quality: int = 60) -> dict:
    """JPEG frame for the live MUR Browser view (not saved to disk)."""
    page = session.page
    data = await page.screenshot(type="jpeg", quality=quality, full_page=False)
    vp = page.viewport_size or {}
    return {"image": "data:image/jpeg;base64," + base64.b64encode(data).decode(), "width": vp.get("width"),
            "height": vp.get("height"), **await page_info(page)}


# --------------------------------------------------------------- download

BLOCKED_EXTENSIONS = {".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".vbs", ".jar", ".apk_unsigned"}


@command("download", 1, 2, timeout=180, needs_browser=False,
         usage="service download https://example.com/file.zip [name]", summary="Download a file into MUR Drive")
async def cmd_download(ctx: CommandContext, args: list):
    raw = _text_arg(args, 0, "URL", 8192)
    engine = ctx.engine
    policy = engine.policy_for(ctx.owner)
    try:
        url = await policy.check_url(raw)
    except BlockedURL as exc:
        raise CommandError(exc.reason, "BlockedURL") from None
    limit = engine.max_download_bytes
    hops = 0
    async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(30, read=60),
                                 headers={"User-Agent": "MURCLOUD-ServiceEngine/10.0"}) as client:
        while True:
            req = client.build_request("GET", url)
            resp = await client.send(req, stream=True)
            if resp.is_redirect:
                await resp.aclose()
                hops += 1
                if hops > 5:
                    raise CommandError("too many redirects")
                nxt = urljoin(url, resp.headers.get("location", ""))
                try:
                    url = await policy.check_url(nxt)
                except BlockedURL as exc:
                    raise CommandError(f"redirect blocked: {exc.reason}", "BlockedURL") from None
                continue
            break
        try:
            if resp.status_code >= 400:
                raise CommandError(f"server answered HTTP {resp.status_code}", "HTTPError")
            declared = int(resp.headers.get("content-length") or 0)
            if declared > limit:
                raise CommandError(f"file is {declared} bytes, limit is {limit}", "TooLarge")
            name = sanitize_filename(str(args[1])) if len(args) > 1 and args[1] else ""
            if not name:
                cd = resp.headers.get("content-disposition", "")
                m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
                name = sanitize_filename(m.group(1) if m else Path(urlsplit(url).path).name, "download")
            if "." not in name:
                ext = mimetypes.guess_extension((resp.headers.get("content-type") or "").split(";")[0].strip())
                name += ext or ".bin"
            if Path(name).suffix.lower() in BLOCKED_EXTENSIONS:
                name += ".blocked"  # never store directly-executable names
            target = unique_path(engine.downloads_dir_for(ctx.owner), name)
            tmp = target.with_name(target.name + ".part")
            sha = hashlib.sha256()
            size = 0
            ctx.emit("browser.download_started", url=url, filename=target.name)
            try:
                with open(tmp, "wb") as fh:
                    async for chunk in resp.aiter_bytes(64 * 1024):
                        size += len(chunk)
                        if size > limit:
                            raise CommandError(f"download exceeded the {limit}-byte limit", "TooLarge")
                        sha.update(chunk)
                        fh.write(chunk)
                tmp.rename(target)
            finally:
                if tmp.exists():
                    tmp.unlink()
        finally:
            await resp.aclose()
    info = {"filename": target.name, "path": str(target), "size": size, "sha256": sha.hexdigest(),
            "content_type": resp.headers.get("content-type"), "url": url}
    ctx.emit("browser.download_finished", **{k: v for k, v in info.items() if k != "path"})
    return info


# --------------------------------------------------------------- events, ai, packages

_EVENT_RE = re.compile(r"^[A-Za-z_][\w.:-]{0,63}$")


@command("event", 1, 2, needs_browser=False, timeout=5, usage="service event page_loaded [data]",
         summary="Emit a custom event")
async def cmd_event(ctx: CommandContext, args: list):
    name = str(args[0])
    if not _EVENT_RE.match(name):
        raise CommandError("event name must be a simple identifier")
    data = args[1] if len(args) > 1 else None
    ctx.emit("custom." + name, name=name, data=data)
    return {"emitted": name}


@command("ai", 1, 2, needs_browser=False, timeout=30, usage="service ai start|status|auto|stop",
         summary="Control the MUR AI agent")
async def cmd_ai(ctx: CommandContext, args: list):
    action = str(args[0]).lower()
    if action not in ("start", "status", "auto", "stop", "chat", "code", "agent"):
        raise CommandError("ai action must be start, status, auto, stop, chat, code or agent")
    controller = ctx.engine.ai_controller
    if controller is None:
        raise CommandError("MUR AI is not attached to this Service Engine", "AIError")
    return await controller(ctx.owner, action, args[1] if len(args) > 1 else None)


@command("install", 1, needs_browser=False, timeout=60, usage="service install <package>",
         summary="Install a MURCLOUD package/driver")
async def cmd_install(ctx: CommandContext, args: list):
    installer = ctx.engine.package_installer
    if installer is None:
        raise CommandError("package manager is not attached to this Service Engine")
    return await installer(ctx.owner, str(args[0]))


def describe_commands() -> list[dict]:
    return [{"verb": s.verb, "usage": s.usage, "summary": s.summary, "timeout": s.timeout}
            for s in REGISTRY.values()]
