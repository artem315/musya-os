# MUSYA language reference (`.mysya`)

MUSYA is the scripting language of MURCLOUD OS. Scripts are parsed by a real
lexer → recursive-descent parser → AST, and executed by an asynchronous
tree-walking interpreter (`musya-runtime/`). There is no `eval`/`exec` of Python
code anywhere; every side effect goes through a permission-checked host.

```mysya
app "Musya Browser"

let url = "https://example.com"

service load url
service wait "Example Domain"
service screenshot

if service.exists("More information"):
    service click "More information"
end

print "МУРГОТОВО!"
```

## Syntax

| Feature | Example |
|---|---|
| comments | `# comment` or `// comment` |
| app name | `app "My App"` |
| variables / constants | `let x = 1`, `const NAME = "Муся"`, `x += 2` |
| numbers, strings, booleans, null | `42`, `3.14`, `"text"`, `'text'`, `"""multi\nline"""`, `true`, `false`, `null` |
| interpolation | `"Hi ${name}, ${1 + 2}"` (escape with `\$`) |
| lists / maps | `[1, 2]`, `{"key": 1, other: 2}`, `xs[0]`, `xs[-1]`, `m.key`, `m["key"]` |
| operators | `+ - * / % **`, `== != < > <= >=`, `and or not`, `in`, `not in`, `c ? a : b` |
| conditions | `if c: … elif c: … else: … end` (colon optional) |
| loops | `while c: … end`, `for x in xs: … end`, `for i, x in xs: … end`, `for k, v in map: … end`, `break`, `continue` |
| functions | `func add(a, b = 0): return a + b end`, lambdas `func(x) => x * 2`, closures |
| exceptions | `try: … catch e: print e.type, e.message finally: … end`, `throw error("msg", "Type")` |
| events | `on page_loaded(info): … end`, `emit "name", data` |
| async | `let t = spawn f(x)`, `await t`, `await [t1, t2]`, `sleep(ms)`, `done(t)` |
| modules | `import "lib/utils.mysya" as utils` → `utils.func()` (names starting with `_` are private) |
| tests | `test "name": assert cond, "message" end` — run with `musya test` / Studio **Test** |

Blocks are closed with `end`; indentation is free. Statements end at a newline
(or `;`). Inside `()`, `[]`, `{}` newlines are ignored.

## Service Engine commands

Statement form (`service verb args…`) or expression form (`let r = service search "x"`,
`service.exists("x")`, `service.text()`, `service.url()`, `service.title()`).

| command | permission | result |
|---|---|---|
| `service start` / `stop` / `status` | browser.navigate / browser.read | session state, tabs, queue |
| `service load URL` | browser.navigate | `{url, title, status}` |
| `service search "text"` | browser.read | `{count, matches:[{tag,text,href,box,visible}]}` |
| `service click "text or #css"` | browser.click | clicks the first visible match (buttons/links preferred) |
| `service type "text" ["field"]` | browser.type | types into the focused or given field |
| `service fill "field" "value"`, `service select "field" "option"` | browser.type / click | |
| `service press Enter` | browser.type | keys: Enter, Tab, Escape, arrows, `Control+A`… |
| `service scroll down 500` | browser.click | `up/down/left/right/top/bottom` |
| `service wait "text" [ms]` / `service wait 500` | browser.read | waits for visible text or the page title |
| `service screenshot ["name"]` | browser.screenshot | PNG saved, copied to MUR Drive `/Screenshots` |
| `service download URL ["name"]` | browser.download | SSRF-checked streaming download into MUR Drive `/Downloads` |
| `service event name [data]` | events.emit | custom event on the MURCLOUD event bus |
| `service tab new [url]` / `list` / `switch N` / `close [N]` | browser.navigate | |
| `service back` / `forward` / `reload` | browser.navigate | |
| `service text ["selector"]`, `html`, `url`, `title`, `errors` | browser.read | page content, console errors, blocked requests |
| `service click_at X Y`, `hover`, `dblclick` | browser.click | |
| `service ai start/status/auto/stop/chat/code/agent` | ai.control | controls the MUR AI agent |
| `service install package` | packages.install | installs packages that need no sensitive permission |

Word arguments (`Enter`, `down`, `start`) are literal; pass a variable in
parentheses: `service press (key)`.

## Built-ins

`print len str num int float bool type range keys values min max abs round now sleep error done`

Modules: `math` (floor, ceil, sqrt, sin, cos, tan, log, random, randint, pow, pi, e),
`json` (parse, stringify), `file` (read, write, append, exists, list, delete, mkdir — sandboxed
to the project, permissions `files.read` / `files.write`), `ai` (`ai.ask(prompt)` — permission `ai.use`).

Methods — strings: `upper lower trim split replace contains starts_with ends_with index_of slice repeat length`;
lists: `push pop insert remove_at contains index_of join slice reverse sort map filter reduce first last length`;
maps: `keys values has get remove length`.

## Sandbox limits

Step budget (2 000 000 per run), call depth 120, collections ≤ 1 000 000 items, strings ≤ 10 MB,
exponent limits, event queue ≤ 1000, 256 concurrent tasks, wall-clock timeout set by the host
(Studio: 600 s, terminal line: 180 s). Limit errors cannot be caught by `try`.

## `.yml` and `.driver` manifests

```yaml
name: MUR Browser
version: 1.0.0          # semver
runtime: musya
entry: main.mysya
type: driver            # app | driver | service (.driver defaults to driver)
permissions:
  - browser.read
  - browser.click
  - browser.screenshot
tests: [site_test.mysya]
env: {START_URL: https://example.com}
```

Validated with `python -m musya_runtime validate FILE` or Studio **Build**. Unknown
permissions, bad versions, missing entry files and `..` paths are errors; secrets in
`env` produce a warning. A script started from Studio runs with exactly the permissions
of its project's `app.yml`; packages run with the permissions approved at install time.

## CLI

```bash
pip install -e musya-runtime -e service-engine
musya run examples/hello.mysya
musya test examples/hello.mysya
musya run examples/browser.mysya --engine      # real Chromium via Playwright
musya check file.mysya ; musya ast file.mysya ; musya validate examples/browser.driver
```
