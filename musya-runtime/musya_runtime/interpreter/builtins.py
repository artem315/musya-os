"""Built-in functions, modules (math, json, file, ai, time) and type methods."""

from __future__ import annotations

import asyncio
import json
import math
import random
import time
from typing import TYPE_CHECKING

from ..errors import MusyaLimitError, MusyaRuntimeError
from .values import (BuiltinFunction, Module, MusyaFunction, TaskHandle, equals, to_display,
                     to_json_value, truthy, type_name)

if TYPE_CHECKING:  # pragma: no cover
    from . import Interpreter


def _err(interp, msg, node, source, kind="TypeError"):
    return MusyaRuntimeError(msg, node.line, node.col, source, kind=kind)


def _arity(name, args, lo, hi, interp, node, source):
    if not lo <= len(args) <= hi:
        exp = str(lo) if lo == hi else f"{lo}-{hi}"
        raise _err(interp, f"{name}() expects {exp} argument(s), got {len(args)}", node, source)


def _num(name, v, interp, node, source):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise _err(interp, f"{name}() expects a number, got {type_name(v)}", node, source)
    return v


def _str(name, v, interp, node, source):
    if not isinstance(v, str):
        raise _err(interp, f"{name}() expects a string, got {type_name(v)}", node, source)
    return v


def builtin(name: str, lo: int = 0, hi: int | None = None, doc: str = ""):
    hi_ = lo if hi is None else hi

    def deco(fn):
        def wrapper(interp, args, node, source):
            _arity(name, args, lo, hi_, interp, node, source)
            return fn(interp, args, node, source)
        return BuiltinFunction(name, wrapper, doc)
    return deco


# --------------------------------------------------------------- core builtins

@builtin("len", 1, doc="Length of a string, list or map")
def _len(interp, args, node, source):
    v = args[0]
    if isinstance(v, (str, list, dict)):
        return len(v)
    raise _err(interp, f"len() of {type_name(v)}", node, source)


@builtin("str", 1)
def _str_fn(interp, args, node, source):
    return to_display(args[0])


@builtin("num", 1, doc="Convert to number")
def _num_fn(interp, args, node, source):
    v = args[0]
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip().replace("_", "")
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                raise _err(interp, f"cannot convert '{v}' to a number", node, source, "ValueError") from None
    raise _err(interp, f"cannot convert {type_name(v)} to a number", node, source)


@builtin("int", 1)
def _int_fn(interp, args, node, source):
    v = _num_fn.fn(interp, args, node, source)
    if isinstance(v, float) and not math.isfinite(v):
        raise _err(interp, "cannot convert infinity to int", node, source, "ValueError")
    return int(v)


@builtin("bool", 1)
def _bool_fn(interp, args, node, source):
    return truthy(args[0])


@builtin("type", 1)
def _type_fn(interp, args, node, source):
    return type_name(args[0])


@builtin("range", 1, 3, doc="range(end) / range(start, end[, step])")
def _range(interp, args, node, source):
    nums = [_num("range", a, interp, node, source) for a in args]
    if any(isinstance(n, float) and not n.is_integer() for n in nums):
        raise _err(interp, "range() needs whole numbers", node, source)
    nums = [int(n) for n in nums]
    start, stop, step = (0, nums[0], 1) if len(nums) == 1 else (nums[0], nums[1], nums[2] if len(nums) == 3 else 1)
    if step == 0:
        raise _err(interp, "range() step cannot be 0", node, source, "ValueError")
    r = range(start, stop, step)
    if len(r) > interp.limits.max_collection:
        raise MusyaLimitError("range is too large", node.line, node.col, source)
    return list(r)


@builtin("keys", 1)
def _keys(interp, args, node, source):
    if not isinstance(args[0], dict):
        raise _err(interp, "keys() expects a map", node, source)
    return list(args[0].keys())


@builtin("values", 1)
def _values(interp, args, node, source):
    if not isinstance(args[0], dict):
        raise _err(interp, "values() expects a map", node, source)
    return list(args[0].values())


@builtin("print", 0, 64)
async def _print(interp, args, node, source):
    await interp.host.output(" ".join(to_display(a) for a in args))
    return None


@builtin("sleep", 1, doc="sleep(ms) - pause without blocking other tasks")
async def _sleep(interp, args, node, source):
    ms = _num("sleep", args[0], interp, node, source)
    if ms < 0 or ms > 600_000:
        raise _err(interp, "sleep() takes 0..600000 ms", node, source, "ValueError")
    await asyncio.sleep(ms / 1000)
    return None


@builtin("now", 0, doc="Current UNIX time in milliseconds")
def _now(interp, args, node, source):
    return int(time.time() * 1000)


@builtin("min", 1, 64)
def _min(interp, args, node, source):
    vals = args[0] if len(args) == 1 and isinstance(args[0], list) else args
    if not vals:
        raise _err(interp, "min() of empty list", node, source, "ValueError")
    return min(_num("min", v, interp, node, source) for v in vals)


@builtin("max", 1, 64)
def _max(interp, args, node, source):
    vals = args[0] if len(args) == 1 and isinstance(args[0], list) else args
    if not vals:
        raise _err(interp, "max() of empty list", node, source, "ValueError")
    return max(_num("max", v, interp, node, source) for v in vals)


@builtin("abs", 1)
def _abs(interp, args, node, source):
    return abs(_num("abs", args[0], interp, node, source))


@builtin("round", 1, 2)
def _round(interp, args, node, source):
    v = _num("round", args[0], interp, node, source)
    digits = int(_num("round", args[1], interp, node, source)) if len(args) > 1 else 0
    if digits == 0:
        return int(math.floor(v + 0.5))
    return round(v, max(0, min(digits, 15)))


@builtin("error", 1, 2, doc="error(message[, type]) - build an error map for throw")
def _error(interp, args, node, source):
    return {"type": to_display(args[1]) if len(args) > 1 else "Error", "message": to_display(args[0])}


@builtin("done", 1, doc="done(task) - true when a spawned task finished")
def _done(interp, args, node, source):
    if not isinstance(args[0], TaskHandle):
        raise _err(interp, "done() expects a task", node, source)
    return args[0].done


# ------------------------------------------------------------------- modules

def _module(name: str, members: dict) -> Module:
    return Module(name, members)


def make_math() -> Module:
    def one(name, fn):
        @builtin(name, 1)
        def f(interp, args, node, source):
            v = _num(name, args[0], interp, node, source)
            try:
                return fn(v)
            except (ValueError, OverflowError) as exc:
                raise _err(interp, f"math.{name}: {exc}", node, source, "MathError") from None
        return f

    @builtin("random", 0)
    def _random(interp, args, node, source):
        return random.random()

    @builtin("randint", 2)
    def _randint(interp, args, node, source):
        a, b = (int(_num("randint", x, interp, node, source)) for x in args)
        return random.randint(min(a, b), max(a, b))

    @builtin("pow", 2)
    def _pow(interp, args, node, source):
        return interp.binary("**", args[0], args[1], node, source)

    return _module("math", {
        "pi": math.pi, "e": math.e,
        "floor": one("floor", lambda v: int(math.floor(v))),
        "ceil": one("ceil", lambda v: int(math.ceil(v))),
        "sqrt": one("sqrt", math.sqrt),
        "sin": one("sin", math.sin), "cos": one("cos", math.cos), "tan": one("tan", math.tan),
        "log": one("log", math.log), "abs": _abs, "round": _round, "min": _min, "max": _max,
        "random": _random, "randint": _randint, "pow": _pow,
    })


def make_json() -> Module:
    @builtin("parse", 1)
    def _parse(interp, args, node, source):
        text = _str("json.parse", args[0], interp, node, source)
        if len(text) > interp.limits.max_string:
            raise MusyaLimitError("JSON text is too large", node.line, node.col, source)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise _err(interp, f"invalid JSON: {exc.msg} (line {exc.lineno})", node, source, "ValueError") from None

    @builtin("stringify", 1, 2)
    def _stringify(interp, args, node, source):
        indent = int(_num("json.stringify", args[1], interp, node, source)) if len(args) > 1 else None
        return json.dumps(to_json_value(args[0]), ensure_ascii=False, indent=indent)

    return _module("json", {"parse": _parse, "stringify": _stringify})


def make_file() -> Module:
    @builtin("read", 1)
    async def _read(interp, args, node, source):
        return await interp.host.file_read(_str("file.read", args[0], interp, node, source))

    @builtin("write", 2)
    async def _write(interp, args, node, source):
        return await interp.host.file_write(_str("file.write", args[0], interp, node, source), to_display(args[1]))

    @builtin("append", 2)
    async def _append(interp, args, node, source):
        return await interp.host.file_write(_str("file.append", args[0], interp, node, source),
                                            to_display(args[1]), append=True)

    @builtin("exists", 1)
    async def _exists(interp, args, node, source):
        return await interp.host.file_exists(_str("file.exists", args[0], interp, node, source))

    @builtin("list", 0, 1)
    async def _list(interp, args, node, source):
        path = _str("file.list", args[0], interp, node, source) if args else "."
        return await interp.host.file_list(path)

    @builtin("delete", 1)
    async def _delete(interp, args, node, source):
        return await interp.host.file_delete(_str("file.delete", args[0], interp, node, source))

    @builtin("mkdir", 1)
    async def _mkdir(interp, args, node, source):
        return await interp.host.file_mkdir(_str("file.mkdir", args[0], interp, node, source))

    return _module("file", {"read": _read, "write": _write, "append": _append, "exists": _exists,
                            "list": _list, "delete": _delete, "mkdir": _mkdir})


def make_ai() -> Module:
    @builtin("ask", 1, 2, doc="ai.ask(prompt[, options]) - one-shot question to MUR AI")
    async def _ask(interp, args, node, source):
        interp.host.require("ai.use", node.line)
        prompt = _str("ai.ask", args[0], interp, node, source)
        options = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
        return await interp.host.ai_ask(prompt, options)

    return _module("ai", {"ask": _ask})


def install_builtins(interp: Interpreter) -> None:
    g = interp.globals.vars
    for fn in (_len, _str_fn, _num_fn, _int_fn, _bool_fn, _type_fn, _range, _keys, _values, _print,
               _sleep, _now, _min, _max, _abs, _round, _error, _done):
        g[fn.name] = fn
    g["float"] = BuiltinFunction("float", lambda i, a, n, s: float(_num_fn.fn(i, a, n, s)))
    g["math"] = make_math()
    g["json"] = make_json()
    g["file"] = make_file()
    g["ai"] = make_ai()
    g["MURCLOUD_VERSION"] = "10.0.0-beta"
    interp.globals.consts.update(g.keys())


# ------------------------------------------------------------------- methods

def method(table: dict, name: str, lo: int = 0, hi: int | None = None, prop: bool = False):
    hi_ = lo if hi is None else hi

    def deco(fn):
        def wrapper(interp, target, args, node, source):
            _arity(name, args, lo, hi_, interp, node, source)
            return fn(interp, target, args, node, source)
        wrapper.is_property = prop
        table[name] = wrapper
        return fn
    return deco


STR: dict = {}
LIST: dict = {}
MAP: dict = {}
METHODS = {"string": STR, "list": LIST, "map": MAP}


@method(STR, "length", prop=True)
def _s_length(i, s, a, n, src):
    return len(s)


for _name, _fn in {"upper": str.upper, "lower": str.lower, "trim": str.strip,
                   "trim_start": str.lstrip, "trim_end": str.rstrip}.items():
    method(STR, _name)(lambda i, s, a, n, src, _f=_fn: _f(s))


@method(STR, "split", 0, 1)
def _s_split(i, s, a, n, src):
    if not a:
        return s.split()
    sep = _str("split", a[0], i, n, src)
    return list(s) if sep == "" else s.split(sep)


@method(STR, "replace", 2)
def _s_replace(i, s, a, n, src):
    old, new = _str("replace", a[0], i, n, src), to_display(a[1])
    if old == "":
        raise _err(i, "replace() needs a non-empty search string", n, src, "ValueError")
    return i.check_size(s.replace(old, new), n, src)


@method(STR, "contains", 1)
def _s_contains(i, s, a, n, src):
    return _str("contains", a[0], i, n, src) in s


@method(STR, "starts_with", 1)
def _s_starts(i, s, a, n, src):
    return s.startswith(_str("starts_with", a[0], i, n, src))


@method(STR, "ends_with", 1)
def _s_ends(i, s, a, n, src):
    return s.endswith(_str("ends_with", a[0], i, n, src))


@method(STR, "index_of", 1)
def _s_index(i, s, a, n, src):
    return s.find(_str("index_of", a[0], i, n, src))


@method(STR, "slice", 1, 2)
def _s_slice(i, s, a, n, src):
    start = int(_num("slice", a[0], i, n, src))
    end = int(_num("slice", a[1], i, n, src)) if len(a) > 1 else None
    return s[start:end]


@method(STR, "repeat", 1)
def _s_repeat(i, s, a, n, src):
    return i.binary("*", s, a[0], n, src)


@method(LIST, "length", prop=True)
def _l_length(i, l, a, n, src):
    return len(l)


@method(LIST, "push", 1, 64)
def _l_push(i, l, a, n, src):
    if len(l) + len(a) > i.limits.max_collection:
        raise MusyaLimitError("list is too large", n.line, n.col, src)
    l.extend(a)
    return len(l)


@method(LIST, "pop", 0)
def _l_pop(i, l, a, n, src):
    if not l:
        raise _err(i, "pop() from empty list", n, src, "IndexError")
    return l.pop()


@method(LIST, "insert", 2)
def _l_insert(i, l, a, n, src):
    l.insert(int(_num("insert", a[0], i, n, src)), a[1])
    return len(l)


@method(LIST, "remove_at", 1)
def _l_remove_at(i, l, a, n, src):
    return l.pop(i.list_index(l, a[0], n, src))


@method(LIST, "contains", 1)
def _l_contains(i, l, a, n, src):
    return any(equals(x, a[0]) for x in l)


@method(LIST, "index_of", 1)
def _l_index(i, l, a, n, src):
    for idx, x in enumerate(l):
        if equals(x, a[0]):
            return idx
    return -1


@method(LIST, "join", 0, 1)
def _l_join(i, l, a, n, src):
    sep = to_display(a[0]) if a else ""
    return i.check_size(sep.join(to_display(x) for x in l), n, src)


@method(LIST, "slice", 1, 2)
def _l_slice(i, l, a, n, src):
    start = int(_num("slice", a[0], i, n, src))
    end = int(_num("slice", a[1], i, n, src)) if len(a) > 1 else None
    return l[start:end]


@method(LIST, "reverse", 0)
def _l_reverse(i, l, a, n, src):
    return list(reversed(l))


@method(LIST, "first", 0)
def _l_first(i, l, a, n, src):
    return l[0] if l else None


@method(LIST, "last", 0)
def _l_last(i, l, a, n, src):
    return l[-1] if l else None


@method(LIST, "sort", 0, 1)
async def _l_sort(i, l, a, n, src):
    if a:
        keyed = []
        for x in l:
            keyed.append((await i.call_function(a[0], [x], n, src), x))
        items = keyed
    else:
        items = [(x, x) for x in l]
    kinds = {type_name(k) for k, _ in items}
    if len(kinds) > 1 or (kinds and kinds - {"number", "string"}):
        raise _err(i, "sort() needs all numbers or all strings", n, src)
    return [x for _, x in sorted(items, key=lambda kv: kv[0])]


@method(LIST, "map", 1)
async def _l_map(i, l, a, n, src):
    return [await i.call_function(a[0], [x], n, src) for x in list(l)]


@method(LIST, "filter", 1)
async def _l_filter(i, l, a, n, src):
    return [x for x in list(l) if truthy(await i.call_function(a[0], [x], n, src))]


@method(LIST, "reduce", 2)
async def _l_reduce(i, l, a, n, src):
    acc = a[1]
    for x in list(l):
        acc = await i.call_function(a[0], [acc, x], n, src)
    return acc


@method(MAP, "keys", 0)
def _m_keys(i, m, a, n, src):
    return list(m.keys())


@method(MAP, "values", 0)
def _m_values(i, m, a, n, src):
    return list(m.values())


@method(MAP, "has", 1)
def _m_has(i, m, a, n, src):
    return to_display(a[0]) in m


@method(MAP, "get", 1, 2)
def _m_get(i, m, a, n, src):
    return m.get(to_display(a[0]), a[1] if len(a) > 1 else None)


@method(MAP, "remove", 1)
def _m_remove(i, m, a, n, src):
    return m.pop(to_display(a[0]), None)


@method(MAP, "length", prop=True)
def _m_length(i, m, a, n, src):
    return len(m)


__all__ = ["install_builtins", "METHODS", "MusyaFunction"]
