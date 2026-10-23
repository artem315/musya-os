"""Asynchronous tree-walking interpreter for MUSYA.

The interpreter evaluates the AST produced by :mod:`musya_runtime.parser`.
It never uses Python ``eval``/``exec``: every operation is implemented
explicitly, and all side effects go through a :class:`RuntimeHost`.

Sandbox limits (steps, call depth, collection sizes, string sizes) are
enforced by :class:`Limits`; wall-clock limits are applied by the caller with
``asyncio.wait_for`` / task cancellation. The interpreter yields to the event
loop regularly so cancellation works even inside tight loops.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..errors import (MusyaAssertionError, MusyaError, MusyaLimitError, MusyaRuntimeError,
                      MusyaThrow)
from ..parser import ast as A
from ..parser import parse
from .host import RuntimeHost
from .values import (BoundMethod, BuiltinFunction, Module, MusyaFunction, ServiceObject,
                     TaskHandle, equals, from_host_value, to_display, truthy, type_name)


@dataclass
class Limits:
    max_steps: int = 2_000_000
    max_depth: int = 120
    max_collection: int = 1_000_000
    max_string: int = 10_000_000
    max_event_queue: int = 1_000
    yield_every: int = 500


@dataclass
class TestResult:
    name: str
    passed: bool
    error: dict | None = None


@dataclass
class RunResult:
    app_name: str | None
    value: Any
    steps: int
    tests: list = field(default_factory=list)


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    def __init__(self, value: Any):
        self.value = value


class Environment:
    __slots__ = ("vars", "consts", "parent")

    def __init__(self, parent: Environment | None = None):
        self.vars: dict[str, Any] = {}
        self.consts: set[str] = set()
        self.parent = parent

    def lookup(self, name: str) -> Environment | None:
        env: Environment | None = self
        while env is not None:
            if name in env.vars:
                return env
            env = env.parent
        return None

    def get(self, name: str, node: A.Node, source: str) -> Any:
        env = self.lookup(name)
        if env is None:
            raise MusyaRuntimeError(f"'{name}' is not defined", node.line, node.col, source,
                                    kind="NameError")
        return env.vars[name]

    def declare(self, name: str, value: Any, const: bool, node: A.Node, source: str) -> None:
        if name in self.consts:
            raise MusyaRuntimeError(f"cannot redeclare constant '{name}'", node.line, node.col, source,
                                    kind="NameError")
        self.vars[name] = value
        if const:
            self.consts.add(name)

    def assign(self, name: str, value: Any, node: A.Node, source: str) -> None:
        env = self.lookup(name)
        if env is None:
            raise MusyaRuntimeError(f"'{name}' is not defined (use 'let {name} = ...' first)",
                                    node.line, node.col, source, kind="NameError")
        if name in env.consts:
            raise MusyaRuntimeError(f"cannot assign to constant '{name}'", node.line, node.col, source,
                                    kind="NameError")
        env.vars[name] = value


class Interpreter:
    def __init__(self, host: RuntimeHost | None = None, limits: Limits | None = None):
        from .builtins import install_builtins  # local import to avoid a cycle

        self.host = host or RuntimeHost()
        self.limits = limits or Limits()
        self.steps = 0
        self.depth = 0
        self.app_name: str | None = None
        self.globals = Environment()
        install_builtins(self)
        self.handlers: dict[str, list[MusyaFunction]] = {}
        self.event_queue: list[tuple[str, Any]] = []
        self._draining = False
        self.tasks: list[TaskHandle] = []
        self.modules: dict[str, Module] = {}
        self._loading: set[str] = set()
        self.tests: list[tuple[A.TestDecl, Environment, str]] = []
        self.collect_tests = False

    # ------------------------------------------------------------------ entry
    async def run_source(self, source: str, source_name: str = "<script>", *,
                         test_mode: bool = False) -> RunResult:
        program = parse(source, source_name)
        return await self.run_program(program, test_mode=test_mode)

    async def run_program(self, program: A.Program, *, test_mode: bool = False) -> RunResult:
        self.collect_tests = test_mode
        env = Environment(self.globals)
        value = None
        try:
            value = await self.exec_block(program.body, env, program.source_name)
            await self.drain_events(program.source_name)
            await self.join_tasks()
        except RecursionError:
            self.cancel_tasks()
            raise MusyaLimitError("call stack exhausted", source_name=program.source_name) from None
        except BaseException:
            self.cancel_tasks()
            raise
        results: list[TestResult] = []
        if test_mode:
            for decl, tenv, src in self.tests:
                try:
                    await self.exec_block(decl.body, Environment(tenv), src)
                    await self.drain_events(src)
                    results.append(TestResult(decl.name, True))
                except _Return:
                    results.append(TestResult(decl.name, True))
                except MusyaLimitError:
                    raise
                except MusyaError as exc:
                    results.append(TestResult(decl.name, False, exc.to_dict()))
        return RunResult(self.app_name, value, self.steps, results)

    def deliver_event(self, name: str, data: Any = None) -> None:
        """Called by the host when an engine event happens (e.g. page_loaded)."""
        if name in self.handlers or "*" in self.handlers:
            if len(self.event_queue) >= self.limits.max_event_queue:
                return
            self.event_queue.append((name, data))

    def cancel_tasks(self) -> None:
        for handle in self.tasks:
            if not handle.task.done():
                handle.task.cancel()
        self.tasks = []

    async def join_tasks(self) -> None:
        while self.tasks:
            pending = self.tasks
            self.tasks = []
            for handle in pending:
                await handle.task  # re-raises the first failure

    # ----------------------------------------------------------- bookkeeping
    async def tick(self, node: A.Node, source: str) -> None:
        self.steps += 1
        if self.steps > self.limits.max_steps:
            raise MusyaLimitError(f"step limit of {self.limits.max_steps} exceeded", node.line, node.col,
                                  source)
        if self.steps % self.limits.yield_every == 0:
            await asyncio.sleep(0)

    def check_size(self, value: Any, node: A.Node, source: str) -> Any:
        if isinstance(value, str) and len(value) > self.limits.max_string:
            raise MusyaLimitError("string is too large", node.line, node.col, source)
        if isinstance(value, (list, dict)) and len(value) > self.limits.max_collection:
            raise MusyaLimitError("collection is too large", node.line, node.col, source)
        return value

    def rt_error(self, msg: str, node: A.Node, source: str, kind: str = "RuntimeError") -> MusyaRuntimeError:
        return MusyaRuntimeError(msg, node.line, node.col, source, kind=kind)

    async def drain_events(self, source: str) -> None:
        if self._draining or not self.event_queue:
            return
        self._draining = True
        try:
            while self.event_queue:
                name, data = self.event_queue.pop(0)
                for handler in list(self.handlers.get(name, [])) + list(self.handlers.get("*", [])):
                    args = [data] if handler.params else []
                    if handler.params and len(handler.params) > 1:
                        args = [data, name]
                    await self.call_function(handler, args, A.Node(), source)
        finally:
            self._draining = False

    # ------------------------------------------------------------ statements
    async def exec_block(self, body: list, env: Environment, source: str) -> Any:
        last = None
        for stmt in body:
            last = await self.exec_stmt(stmt, env, source)
            if self.event_queue and not self._draining:
                await self.drain_events(source)
        return last

    async def exec_stmt(self, node: A.Node, env: Environment, source: str) -> Any:
        await self.tick(node, source)
        method = getattr(self, "s_" + type(node).__name__)
        return await method(node, env, source)

    async def s_ExprStmt(self, node: A.ExprStmt, env, source):
        return await self.eval(node.expr, env, source)

    async def s_AppDecl(self, node: A.AppDecl, env, source):
        self.app_name = to_display(await self.eval(node.name, env, source))
        return None

    async def s_Let(self, node: A.Let, env, source):
        value = await self.eval(node.value, env, source)
        env.declare(node.name, value, node.const, node, source)
        return None

    async def s_Assign(self, node: A.Assign, env, source):
        value = await self.eval(node.value, env, source)
        target = node.target
        if node.op != "=":
            current = await self.eval(target, env, source)
            value = self.binary(node.op[:-1], current, value, node, source)
        if isinstance(target, A.Name):
            env.assign(target.id, value, node, source)
        elif isinstance(target, A.Member):
            obj = await self.eval(target.obj, env, source)
            if not isinstance(obj, dict):
                raise self.rt_error(f"cannot set property on {type_name(obj)}", node, source, "TypeError")
            obj[target.name] = value
            self.check_size(obj, node, source)
        elif isinstance(target, A.Index):
            obj = await self.eval(target.obj, env, source)
            idx = await self.eval(target.index, env, source)
            if isinstance(obj, list):
                i = self.list_index(obj, idx, node, source)
                obj[i] = value
            elif isinstance(obj, dict):
                if not isinstance(idx, (str, int, float)) or isinstance(idx, bool):
                    raise self.rt_error("map keys must be strings or numbers", node, source, "TypeError")
                obj[to_display(idx)] = value
                self.check_size(obj, node, source)
            else:
                raise self.rt_error(f"cannot index-assign {type_name(obj)}", node, source, "TypeError")
        return None

    async def s_Print(self, node: A.Print, env, source):
        values = [await self.eval(v, env, source) for v in node.values]
        await self.host.output(" ".join(to_display(v) for v in values))
        return None

    async def s_If(self, node: A.If, env, source):
        for cond, body in node.branches:
            if truthy(await self.eval(cond, env, source)):
                return await self.exec_block(body, Environment(env), source)
        if node.otherwise is not None:
            return await self.exec_block(node.otherwise, Environment(env), source)
        return None

    async def s_While(self, node: A.While, env, source):
        while truthy(await self.eval(node.cond, env, source)):
            await self.tick(node, source)
            try:
                await self.exec_block(node.body, Environment(env), source)
            except _Break:
                break
            except _Continue:
                continue
        return None

    async def s_For(self, node: A.For, env, source):
        iterable = await self.eval(node.iterable, env, source)
        if isinstance(iterable, dict):
            items = list(iterable.items()) if node.var2 else list(iterable.keys())
        elif isinstance(iterable, list):
            items = list(enumerate(iterable)) if node.var2 else list(iterable)
        elif isinstance(iterable, str):
            items = list(enumerate(iterable)) if node.var2 else list(iterable)
        else:
            raise self.rt_error(f"cannot loop over {type_name(iterable)}", node, source, "TypeError")
        for item in items:
            await self.tick(node, source)
            loop_env = Environment(env)
            if node.var2:
                loop_env.vars[node.var], loop_env.vars[node.var2] = item
            else:
                loop_env.vars[node.var] = item
            try:
                await self.exec_block(node.body, loop_env, source)
            except _Break:
                break
            except _Continue:
                continue
        return None

    async def s_FuncDecl(self, node: A.FuncDecl, env, source):
        fn = MusyaFunction(node.func.name, node.func.params, node.func.body, env, source)
        env.declare(node.func.name, fn, False, node, source)
        return None

    async def s_Return(self, node: A.Return, env, source):
        value = await self.eval(node.value, env, source) if node.value is not None else None
        raise _Return(value)

    async def s_Break(self, node, env, source):
        raise _Break()

    async def s_Continue(self, node, env, source):
        raise _Continue()

    async def s_Throw(self, node: A.Throw, env, source):
        value = await self.eval(node.value, env, source)
        raise MusyaThrow(value, node.line, node.col, source)

    async def s_Try(self, node: A.Try, env, source):
        try:
            return await self.exec_block(node.body, Environment(env), source)
        except MusyaLimitError:
            raise
        except MusyaError as exc:
            if node.catch_body is None:
                raise
            catch_env = Environment(env)
            if node.catch_name:
                catch_env.vars[node.catch_name] = exc.value if isinstance(exc, MusyaThrow) else exc.to_dict()
            return await self.exec_block(node.catch_body, catch_env, source)
        finally:
            if node.finally_body is not None:
                await self.exec_block(node.finally_body, Environment(env), source)

    async def s_Import(self, node: A.Import, env, source):
        module = await self.import_module(node.path, source, node)
        alias = node.alias or module.name
        env.declare(alias, module, False, node, source)
        return None

    async def s_On(self, node: A.On, env, source):
        event = to_display(await self.eval(node.event, env, source))
        params = [A.Param(node.param)] if node.param else []
        handler = MusyaFunction(f"on {event}", params, node.body, env, source)
        self.handlers.setdefault(event, []).append(handler)
        return None

    async def s_Emit(self, node: A.Emit, env, source):
        self.host.require("events.emit", node.line)
        event = to_display(await self.eval(node.event, env, source))
        data = await self.eval(node.data, env, source) if node.data is not None else None
        if len(self.event_queue) >= self.limits.max_event_queue:
            raise MusyaLimitError("event queue is full", node.line, node.col, source)
        self.event_queue.append((event, data))
        await self.host.emit(event, data)
        return None

    async def s_Assert(self, node: A.Assert, env, source):
        if not truthy(await self.eval(node.cond, env, source)):
            msg = "assertion failed"
            if node.message is not None:
                msg = to_display(await self.eval(node.message, env, source))
            raise MusyaAssertionError(msg, node.line, node.col, source)
        return None

    async def s_TestDecl(self, node: A.TestDecl, env, source):
        if self.collect_tests:
            self.tests.append((node, env, source))
        return None

    # ----------------------------------------------------------- expressions
    async def eval(self, node: A.Node, env: Environment, source: str) -> Any:
        method = getattr(self, "e_" + type(node).__name__)
        return await method(node, env, source)

    async def e_Literal(self, node: A.Literal, env, source):
        return node.value

    async def e_TemplateString(self, node: A.TemplateString, env, source):
        out = []
        for part in node.parts:
            out.append(part if isinstance(part, str) else to_display(await self.eval(part, env, source)))
        return self.check_size("".join(out), node, source)

    async def e_Name(self, node: A.Name, env, source):
        return env.get(node.id, node, source)

    async def e_ListExpr(self, node: A.ListExpr, env, source):
        return self.check_size([await self.eval(x, env, source) for x in node.items], node, source)

    async def e_MapExpr(self, node: A.MapExpr, env, source):
        out = {}
        for k, v in node.pairs:
            key = await self.eval(k, env, source)
            out[to_display(key)] = await self.eval(v, env, source)
        return out

    async def e_Unary(self, node: A.Unary, env, source):
        value = await self.eval(node.operand, env, source)
        if node.op == "not":
            return not truthy(value)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise self.rt_error(f"unary '{node.op}' needs a number, got {type_name(value)}", node, source,
                                "TypeError")
        return -value if node.op == "-" else +value

    async def e_Logical(self, node: A.Logical, env, source):
        left = await self.eval(node.left, env, source)
        if node.op == "and":
            return await self.eval(node.right, env, source) if truthy(left) else left
        return left if truthy(left) else await self.eval(node.right, env, source)

    async def e_Ternary(self, node: A.Ternary, env, source):
        if truthy(await self.eval(node.cond, env, source)):
            return await self.eval(node.then, env, source)
        return await self.eval(node.otherwise, env, source)

    async def e_Binary(self, node: A.Binary, env, source):
        left = await self.eval(node.left, env, source)
        right = await self.eval(node.right, env, source)
        return self.binary(node.op, left, right, node, source)

    def binary(self, op: str, a: Any, b: Any, node: A.Node, source: str) -> Any:
        def num(x):
            return isinstance(x, (int, float)) and not isinstance(x, bool)

        if op == "==":
            return equals(a, b)
        if op == "!=":
            return not equals(a, b)
        if op in ("in", "not in"):
            if isinstance(b, (list, str, dict)):
                if isinstance(b, str):
                    if not isinstance(a, str):
                        raise self.rt_error("'in' on a string needs a string", node, source, "TypeError")
                    res = a in b
                elif isinstance(b, dict):
                    res = to_display(a) in b
                else:
                    res = any(equals(a, x) for x in b)
                return res if op == "in" else not res
            raise self.rt_error(f"'{op}' needs a list, string or map, got {type_name(b)}", node, source,
                                "TypeError")
        if op == "+":
            if num(a) and num(b):
                return a + b
            if isinstance(a, str) or isinstance(b, str):
                return self.check_size(to_display(a) + to_display(b), node, source)
            if isinstance(a, list) and isinstance(b, list):
                return self.check_size(a + b, node, source)
            if isinstance(a, dict) and isinstance(b, dict):
                return self.check_size({**a, **b}, node, source)
        elif op == "*":
            if num(a) and num(b):
                return a * b
            if (isinstance(a, (str, list)) and isinstance(b, int) and not isinstance(b, bool)) or \
                    (isinstance(b, (str, list)) and isinstance(a, int) and not isinstance(a, bool)):
                seq, n = (a, b) if isinstance(a, (str, list)) else (b, a)
                limit = self.limits.max_string if isinstance(seq, str) else self.limits.max_collection
                if len(seq) * max(n, 0) > limit:
                    raise MusyaLimitError("repetition result is too large", node.line, node.col, source)
                return seq * n
        elif op in ("-", "/", "%", "**"):
            if num(a) and num(b):
                if op == "-":
                    return a - b
                if op in ("/", "%") and b == 0:
                    raise self.rt_error("division by zero", node, source, "MathError")
                if op == "/":
                    res = a / b
                    return int(res) if isinstance(a, int) and isinstance(b, int) and a % b == 0 else res
                if op == "%":
                    return a % b
                if abs(b) > 10_000 or (isinstance(a, int) and isinstance(b, int) and b > 0 and
                                       abs(a) > 1 and b * max(abs(a).bit_length(), 1) > 100_000):
                    raise MusyaLimitError("exponent is too large", node.line, node.col, source)
                try:
                    return a ** b
                except (OverflowError, ZeroDivisionError) as exc:
                    raise self.rt_error(str(exc), node, source, "MathError") from None
        elif op in ("<", ">", "<=", ">="):
            if (num(a) and num(b)) or (isinstance(a, str) and isinstance(b, str)):
                return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        raise self.rt_error(f"cannot apply '{op}' to {type_name(a)} and {type_name(b)}", node, source,
                            "TypeError")

    async def e_Member(self, node: A.Member, env, source):
        obj = await self.eval(node.obj, env, source)
        return self.get_member(obj, node.name, node, source)

    def get_member(self, obj: Any, name: str, node: A.Node, source: str) -> Any:
        from .builtins import METHODS

        if isinstance(obj, Module):
            if name in obj.exports:
                return obj.exports[name]
            raise self.rt_error(f"module '{obj.name}' has no member '{name}'", node, source, "NameError")
        if isinstance(obj, ServiceObject):
            return BoundMethod(f"service.{name}", obj, _service_member(name))
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        table = METHODS.get(type_name(obj), {})
        if name in table:
            spec = table[name]
            if getattr(spec, "is_property", False):
                return spec(self, obj, [], node, source)
            return BoundMethod(name, obj, spec)
        if isinstance(obj, dict):
            return None
        raise self.rt_error(f"{type_name(obj)} has no property '{name}'", node, source, "TypeError")

    async def e_Index(self, node: A.Index, env, source):
        obj = await self.eval(node.obj, env, source)
        idx = await self.eval(node.index, env, source)
        if isinstance(obj, (list, str)):
            return obj[self.list_index(obj, idx, node, source)]
        if isinstance(obj, dict):
            return obj.get(to_display(idx))
        if isinstance(obj, Module):
            return self.get_member(obj, to_display(idx), node, source)
        raise self.rt_error(f"cannot index {type_name(obj)}", node, source, "TypeError")

    def list_index(self, seq, idx, node, source) -> int:
        if isinstance(idx, float) and idx.is_integer():
            idx = int(idx)
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise self.rt_error("index must be a whole number", node, source, "TypeError")
        if idx < 0:
            idx += len(seq)
        if not 0 <= idx < len(seq):
            raise self.rt_error(f"index {idx} out of range (length {len(seq)})", node, source, "IndexError")
        return idx

    async def e_FuncExpr(self, node: A.FuncExpr, env, source):
        return MusyaFunction(node.name, node.params, node.body, env, source)

    async def e_ServiceRef(self, node, env, source):
        return ServiceObject()

    async def e_ServiceCommand(self, node: A.ServiceCommand, env, source):
        args = [await self.eval(a, env, source) for a in node.args]
        return await self.service_call(node.verb, args, node, source)

    async def service_call(self, verb: str, args: list, node: A.Node, source: str) -> Any:
        try:
            result = await self.host.service(verb, args, line=node.line)
        except MusyaError as exc:
            if exc.line is None:
                exc.line, exc.column = node.line, node.col
            if exc.source_name is None:
                exc.source_name = source
            raise
        return from_host_value(result)

    async def e_Call(self, node: A.Call, env, source):
        callee = await self.eval(node.callee, env, source)
        args = [await self.eval(a, env, source) for a in node.args]
        return await self.call_function(callee, args, node, source)

    async def call_function(self, fn: Any, args: list, node: A.Node, source: str) -> Any:
        await self.tick(node, source)
        if isinstance(fn, BuiltinFunction):
            return await self._call_native(lambda: fn.fn(self, args, node, source), fn.name, node, source)
        if isinstance(fn, BoundMethod):
            return await self._call_native(lambda: fn.fn(self, fn.target, args, node, source), fn.name,
                                           node, source)
        if not isinstance(fn, MusyaFunction):
            raise self.rt_error(f"{type_name(fn)} is not callable", node, source, "TypeError")
        required = sum(1 for p in fn.params if p.default is None)
        if len(args) < required or len(args) > len(fn.params):
            expected = f"{required}" if required == len(fn.params) else f"{required}-{len(fn.params)}"
            raise self.rt_error(f"{fn.name or 'function'}() expects {expected} argument(s), got {len(args)}",
                                node, source, "TypeError")
        if self.depth >= self.limits.max_depth:
            raise MusyaLimitError(f"maximum call depth ({self.limits.max_depth}) exceeded", node.line,
                                  node.col, source)
        call_env = Environment(fn.closure)
        for i, p in enumerate(fn.params):
            if i < len(args):
                call_env.vars[p.name] = args[i]
            else:
                call_env.vars[p.name] = await self.eval(p.default, call_env, fn.source_name)
        self.depth += 1
        try:
            await self.exec_block(fn.body, call_env, fn.source_name)
        except _Return as ret:
            return ret.value
        finally:
            self.depth -= 1
        return None

    async def _call_native(self, thunk, name: str, node: A.Node, source: str) -> Any:
        try:
            result = thunk()
            if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                result = await result
            return result
        except (MusyaError, _Return, _Break, _Continue, asyncio.CancelledError):
            raise
        except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, OverflowError,
                UnicodeError) as exc:
            raise self.rt_error(f"{name}(): {exc}", node, source, "RuntimeError") from None

    async def e_Await(self, node: A.Await, env, source):
        value = await self.eval(node.expr, env, source)
        if isinstance(value, TaskHandle):
            if value in self.tasks:
                self.tasks.remove(value)
            return await value.task
        if isinstance(value, list) and value and all(isinstance(v, TaskHandle) for v in value):
            out = []
            for h in value:
                if h in self.tasks:
                    self.tasks.remove(h)
                out.append(await h.task)
            return out
        return value

    async def e_Spawn(self, node: A.Spawn, env, source):
        if len(self.tasks) >= 256:
            raise MusyaLimitError("too many concurrent tasks", node.line, node.col, source)
        coro = self.eval(node.call, env, source)
        label = node.call.verb if isinstance(node.call, A.ServiceCommand) else "call"
        handle = TaskHandle(asyncio.ensure_future(coro), label)
        self.tasks.append(handle)
        return handle

    # --------------------------------------------------------------- modules
    async def import_module(self, path: str, importer: str, node: A.Node) -> Module:
        try:
            module_id, src = await self.host.load_module(path, importer)
        except MusyaError as exc:
            exc.line, exc.column, exc.source_name = node.line, node.col, importer
            raise
        if module_id in self.modules:
            return self.modules[module_id]
        if module_id in self._loading:
            raise self.rt_error(f"circular import of '{path}'", node, importer, "ImportError")
        self._loading.add(module_id)
        try:
            program = parse(src, module_id)
            menv = Environment(self.globals)
            await self.exec_block(program.body, menv, module_id)
        finally:
            self._loading.discard(module_id)
        stem = module_id.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        module = Module(stem, {k: v for k, v in menv.vars.items() if not k.startswith("_")})
        self.modules[module_id] = module
        return module


def _service_member(name: str):
    async def call(interp: Interpreter, _target, args, node, source):
        return await interp.service_call(name, list(args), node, source)
    return call


async def run(source: str, host: RuntimeHost | None = None, *, source_name: str = "<script>",
              limits: Limits | None = None, test_mode: bool = False) -> RunResult:
    """Parse and run ``source``. Convenience wrapper used by the CLI and tests."""
    interp = Interpreter(host, limits)
    return await interp.run_source(source, source_name, test_mode=test_mode)


__all__ = ["Interpreter", "Limits", "RunResult", "TestResult", "Environment", "run"]
