import asyncio

import pytest

from musya_runtime import (Interpreter, Limits, MusyaLimitError, MusyaPermissionError,
                           MusyaRuntimeError, MusyaSyntaxError, RuntimeHost, SandboxHost, parse,
                           run, tokenize)
from musya_runtime.parser import ast as A


class RecordingHost(RuntimeHost):
    """Host that records output and service calls (service results are scripted)."""

    def __init__(self, service_results=None, permissions=None):
        super().__init__(permissions)
        self.out: list[str] = []
        self.calls: list[tuple[str, list]] = []
        self.emitted: list[tuple[str, object]] = []
        self.service_results = service_results or {}
        self.interp = None

    async def output(self, text):
        self.out.append(text)

    async def service(self, verb, args, line=None):
        self.calls.append((verb, args))
        res = self.service_results.get(verb, {"ok": True})
        if callable(res):
            res = res(args)
        if verb == "load" and self.interp is not None:
            self.interp.deliver_event("page_loaded", {"url": args[0]})
        return res

    async def emit(self, name, data):
        self.emitted.append((name, data))


async def run_out(src, host=None, **kw):
    host = host or RecordingHost()
    interp = Interpreter(host, kw.pop("limits", None))
    host.interp = interp
    await interp.run_source(src, **kw)
    return host.out


# ------------------------------------------------------------------ lexer

def test_lexer_basic_tokens():
    toks = tokenize('let x = 3.5 # comment\nprint "a\\n${x}"')
    kinds = [(t.type, t.value) for t in toks]
    assert ("KEYWORD", "let") in kinds
    assert ("NUMBER", 3.5) in kinds
    assert ("STRING", "a\n${x}") in kinds
    assert kinds[-1] == ("EOF", None)


def test_lexer_ignores_newlines_in_brackets():
    toks = tokenize("let a = [1,\n 2,\n 3]\n")
    assert [t.type for t in toks].count("NEWLINE") == 1


def test_lexer_errors_have_location():
    with pytest.raises(MusyaSyntaxError) as exc:
        tokenize('print "unterminated')
    assert exc.value.line == 1
    with pytest.raises(MusyaSyntaxError):
        tokenize("let a = (1, 2")
    with pytest.raises(MusyaSyntaxError):
        tokenize("let a = 1 @ 2")


def test_triple_quoted_and_escapes():
    toks = tokenize('let s = """line1\nline2"""\nlet d = "\\$5 \\u{1F431}"')
    strings = [t.value for t in toks if t.type == "STRING"]
    assert strings[0] == "line1\nline2"
    assert strings[1] == "\x00$5 🐱"


# ----------------------------------------------------------------- parser

def test_parse_spec_example():
    src = '''app "Musya Browser"

let url = "https://example.com"

service load url
service wait "Example Domain"
service screenshot

if service.exists("More information"):
    service click "More information"
end

print "МУРГОТОВО!"
'''
    prog = parse(src)
    kinds = [type(s).__name__ for s in prog.body]
    assert kinds == ["AppDecl", "Let", "ExprStmt", "ExprStmt", "ExprStmt", "If", "Print"]
    load = prog.body[2].expr
    assert isinstance(load, A.ServiceCommand) and load.verb == "load"
    assert isinstance(load.args[0], A.Name)  # variable, not literal


def test_service_word_slots():
    prog = parse("service press Enter\nservice scroll down 500\nservice ai auto\nservice event page_loaded")
    cmds = [s.expr for s in prog.body]
    assert cmds[0].args[0].value == "Enter"
    assert cmds[1].args[0].value == "down" and cmds[1].args[1].value == 500
    assert cmds[2].verb == "ai" and cmds[2].args[0].value == "auto"
    assert cmds[3].args[0].value == "page_loaded"


def test_parse_errors():
    for bad in ["if x:\n print 1\n", "end", "return 1", "break", "let = 3", "func f(a, a): end",
                "service load", "service press Enter Space", "1 = 2", "try:\n print 1\nend"]:
        with pytest.raises(MusyaSyntaxError):
            parse(bad)


def test_operator_precedence():
    prog = parse("let x = 1 + 2 * 3 ** 2")
    expr = prog.body[0].value
    assert expr.op == "+" and expr.right.op == "*" and expr.right.right.op == "**"


# ------------------------------------------------------------ interpreter

async def test_arithmetic_and_strings():
    out = await run_out('print 1 + 2 * 3, 10 / 4, 10 / 2, 7 % 3, -2 ** 2, "a" + 1, "ab" * 2')
    assert out == ["7 2.5 5 1 -4 a1 abab"]


async def test_variables_consts_and_scopes():
    out = await run_out('''
let a = 1
const B = 2
if true:
    let a = 10
    a += 5
    print a
end
a = a + B
print a
''')
    assert out == ["15", "3"]
    with pytest.raises(MusyaRuntimeError, match="constant"):
        await run_out("const X = 1\nX = 2")
    with pytest.raises(MusyaRuntimeError, match="not defined"):
        await run_out("y = 3")


async def test_control_flow():
    out = await run_out('''
let total = 0
for i in range(10):
    if i == 3:
        continue
    elif i == 6:
        break
    end
    total += i
end
let n = 0
while n < 5:
    n += 1
end
print total, n
for k, v in {"x": 1, "y": 2}:
    print k + "=" + str(v)
end
''')
    assert out == ["12 5", "x=1", "y=2"]


async def test_functions_closures_defaults_recursion():
    out = await run_out('''
func counter():
    let c = 0
    return func() => c
end
func make_adder(n, base = 100):
    func add(x):
        return x + n + base
    end
    return add
end
func fact(n):
    if n <= 1:
        return 1
    end
    return n * fact(n - 1)
end
let add5 = make_adder(5)
print add5(1), make_adder(1, 0)(1), fact(20), counter()()
''')
    assert out == ["106 2 2432902008176640000 0"]


async def test_collections_and_methods():
    out = await run_out('''
let xs = [3, 1, 2]
xs.push(5)
let m = {"name": "Муся", tags: []}
m.tags.push("cat")
m["age"] = 3
print xs.sort(), xs.length, xs.filter(func(x) => x > 2), xs.reduce(func(a, b) => a + b, 0)
print m.name.upper(), m.tags, m.has("age"), m.get("nope", "default"), len(m)
print "a,b,c".split(","), "  hi ".trim(), "Мур".contains("ур"), [1, [2]] == [1, [2]]
print xs[-1], "hello"[0], 2 in xs, "z" not in "abc"
''')
    assert out == [
        "[1, 2, 3, 5] 4 [3, 5] 11",
        'МУСЯ ["cat"] true default 3',
        '["a", "b", "c"] hi true true',
        "5 h true true",
    ]


async def test_try_catch_finally_throw():
    out = await run_out('''
func risky(x):
    if x > 1:
        throw {"type": "Too big", "message": "x=" + str(x)}
    end
    return x
end
try:
    risky(5)
catch e:
    print e.type, e.message
finally:
    print "cleanup"
end
try:
    [1][5]
catch err:
    print err.type
end
''')
    assert out == ["Too big x=5", "cleanup", "IndexError"]


async def test_uncaught_errors_have_line_numbers():
    with pytest.raises(MusyaRuntimeError) as exc:
        await run_out('let a = 1\nlet b = a + "x" - 2\n')
    assert exc.value.line == 2
    assert exc.value.kind == "TypeError"


async def test_template_strings():
    out = await run_out('let n = "Муся"\nlet p = {"k": [1,2]}\nprint "Hi ${n}! ${p.k[1] * 10} \\${raw}"')
    assert out == ["Hi Муся! 20 ${raw}"]


async def test_service_commands_reach_host():
    host = RecordingHost({"exists": lambda a: a[0] == "More information",
                          "screenshot": {"path": "shot.png"}})
    out = await run_out('''
app "Musya Browser"
let url = "https://example.com"
service load url
service wait "Example Domain"
let shot = service screenshot
if service.exists("More information"):
    service click "More information"
end
service type "Hello from MURCLOUD"
service press Enter
service scroll down 500
print shot.path, "МУРГОТОВО!"
''', host)
    verbs = [c[0] for c in host.calls]
    assert verbs == ["load", "wait", "screenshot", "exists", "click", "type", "press", "scroll"]
    assert host.calls[0][1] == ["https://example.com"]
    assert host.calls[7][1] == ["down", 500]
    assert out == ["shot.png МУРГОТОВО!"]


async def test_events_from_host_and_emit():
    host = RecordingHost()
    out = await run_out('''
on page_loaded(info):
    print "loaded " + info.url
end
on "custom"(d):
    print "custom", d
end
service load "https://example.com"
emit "custom", {"n": 1}
print "end"
''', host)
    assert out == ["loaded https://example.com", 'custom {"n": 1}', "end"]
    assert host.emitted == [("custom", {"n": 1})]


async def test_async_spawn_await():
    out = await run_out('''
func slow(x):
    sleep(20)
    return x * 2
end
let a = spawn slow(1)
let b = spawn slow(2)
print await [a, b]
let c = spawn slow(5)
print done(c) == false
print await c
''')
    assert out == ["[2, 4]", "true", "10"]


async def test_tests_mode():
    host = RecordingHost()
    interp = Interpreter(host)
    res = await interp.run_source('''
func add(a, b):
    return a + b
end
test "adds":
    assert add(1, 2) == 3
end
test "fails":
    assert add(1, 2) == 4, "math is broken"
end
''', test_mode=True)
    assert [(t.name, t.passed) for t in res.tests] == [("adds", True), ("fails", False)]
    assert res.tests[1].error["message"] == "math is broken"


async def test_sandbox_limits():
    with pytest.raises(MusyaLimitError):
        await run_out("while true:\nend", limits=Limits(max_steps=10_000))
    with pytest.raises(MusyaLimitError):
        await run_out("func f(n):\n return f(n + 1)\nend\nf(0)")
    with pytest.raises(MusyaLimitError):
        await run_out('let s = "x" * 100000000')
    with pytest.raises(MusyaLimitError):
        await run_out("let n = 10 ** 100000")
    with pytest.raises(MusyaLimitError):
        await run_out("let r = range(100000000)")
    # Limit errors are not catchable by scripts.
    with pytest.raises(MusyaLimitError):
        await run_out("try:\n while true:\n end\ncatch e:\n print 1\nend", limits=Limits(max_steps=5000))


async def test_deep_recursion_within_limit_works():
    out = await run_out("func d(n):\n if n == 0:\n  return 0\n end\n return 1 + d(n - 1)\nend\nprint d(110)")
    assert out == ["110"]


async def test_cancellation_of_busy_loop():
    interp = Interpreter(RecordingHost())
    task = asyncio.ensure_future(interp.run_source("let i = 0\nwhile true:\n i += 1\nend"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_no_python_escape():
    # Attribute access is limited to MUSYA members; Python internals are unreachable.
    for src in ['print "".__class__', "print len.fn", 'print math.__dict__', 'print (1).real']:
        with pytest.raises((MusyaRuntimeError, MusyaSyntaxError)):
            await run_out(src)


async def test_without_engine_service_fails_cleanly():
    with pytest.raises(MusyaRuntimeError, match="Service Engine is not connected"):
        await run('service load "https://example.com"', RuntimeHost())


# --------------------------------------------------------------- sandbox

async def test_sandbox_files_and_imports(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "utils.mysya").write_text('let greeting = "Мур"\nfunc shout(s):\n return s.upper() + "!"\nend\nlet _hidden = 1\n')
    out: list[str] = []
    host = SandboxHost(tmp_path, output_sink=out.append)
    await run('''
import "lib/utils.mysya" as u
file.write("site/index.html", "<h1>${u.greeting}</h1>")
print file.read("site/index.html"), file.exists("site/index.html"), file.list("site")
print u.shout("муся")
file.delete("site/index.html")
print file.exists("site/index.html")
''', host, source_name="main.mysya")
    assert out == ['<h1>Мур</h1> true ["index.html"]', "МУСЯ!", "false"]


async def test_sandbox_blocks_escape_and_permissions(tmp_path):
    host = SandboxHost(tmp_path / "proj", output_sink=lambda s: None)
    for src in ['file.read("../secret.txt")', 'file.read("a/../../x")', 'file.list("..")']:
        with pytest.raises(MusyaPermissionError):
            await run(src, host)
    # Absolute paths are interpreted relative to the project root, never the real filesystem.
    await run('file.write("/etc/passwd", "x")', host)
    assert (tmp_path / "proj" / "etc" / "passwd").read_text() == "x"
    ro = SandboxHost(tmp_path / "proj", permissions={"files.read"}, output_sink=lambda s: None)
    with pytest.raises(MusyaPermissionError, match="files.write"):
        await run('file.write("a.txt", "x")', ro)
    with pytest.raises(MusyaPermissionError, match="ai.use"):
        await run('ai.ask("hi")', ro)
    # Permission errors are catchable so scripts can degrade gracefully.
    out = []
    ro.output_sink = out.append
    await run('try:\n file.write("a.txt", "x")\ncatch e:\n print e.type\nend', ro)
    assert out == ["PermissionError"]


async def test_circular_import(tmp_path):
    (tmp_path / "a.mysya").write_text('import "b.mysya"\n')
    (tmp_path / "b.mysya").write_text('import "a.mysya"\n')
    with pytest.raises(MusyaRuntimeError, match="circular"):
        await run('import "a.mysya"', SandboxHost(tmp_path), source_name="main.mysya")
