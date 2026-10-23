"""Runtime value model and helpers for MUSYA.

MUSYA values map onto plain Python objects so they can be passed to the
Service Engine and serialised to JSON without conversion:

=========  ===================
MUSYA      Python
=========  ===================
null       ``None``
bool       ``bool``
number     ``int`` / ``float``
string     ``str``
list       ``list``
map        ``dict`` (string keys)
function   :class:`MusyaFunction` / :class:`BuiltinFunction`
module     :class:`Module`
task       :class:`TaskHandle`
=========  ===================
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class BuiltinFunction:
    name: str
    fn: Callable[..., Any]  # (interp, args, node) -> value | awaitable
    doc: str = ""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<builtin {self.name}>"


@dataclass
class MusyaFunction:
    name: str | None
    params: list
    body: list
    closure: Any  # Environment
    source_name: str = "<script>"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<func {self.name or 'anonymous'}>"


@dataclass
class BoundMethod:
    name: str
    target: Any
    fn: Callable[..., Any]  # (interp, target, args, node)


@dataclass
class Module:
    name: str
    exports: dict = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<module {self.name}>"


@dataclass
class TaskHandle:
    task: asyncio.Task
    label: str = "task"

    @property
    def done(self) -> bool:
        return self.task.done()


@dataclass
class ServiceObject:
    """Value of the bare ``service`` keyword (``service.exists(...)``)."""


Awaitableish = Awaitable[Any] | Any


def type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "map"
    if isinstance(v, (MusyaFunction, BuiltinFunction, BoundMethod)):
        return "function"
    if isinstance(v, Module):
        return "module"
    if isinstance(v, TaskHandle):
        return "task"
    if isinstance(v, ServiceObject):
        return "service"
    return "object"


def truthy(v: Any) -> bool:
    if v is None or v is False:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, (str, list, dict)):
        return len(v) > 0
    return True


def equals(a: Any, b: Any) -> bool:
    ta, tb = type_name(a), type_name(b)
    if ta != tb:
        return False
    if ta == "list":
        return len(a) == len(b) and all(equals(x, y) for x, y in zip(a, b))
    if ta == "map":
        return a.keys() == b.keys() and all(equals(a[k], b[k]) for k in a)
    return a == b


def format_number(n: int | float) -> str:
    if isinstance(n, bool):
        return "true" if n else "false"
    if isinstance(n, int):
        return str(n)
    if math.isnan(n):
        return "NaN"
    if math.isinf(n):
        return "Infinity" if n > 0 else "-Infinity"
    if n.is_integer() and abs(n) < 1e16:
        return str(int(n))
    return repr(n)


def to_display(v: Any, nested: bool = False) -> str:
    """Convert a value to the text ``print`` shows."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return format_number(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False) if nested else v
    if isinstance(v, list):
        return "[" + ", ".join(to_display(x, True) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{json.dumps(str(k), ensure_ascii=False)}: {to_display(x, True)}"
                               for k, x in v.items()) + "}"
    if isinstance(v, MusyaFunction):
        return f"<func {v.name or 'anonymous'}>"
    if isinstance(v, (BuiltinFunction, BoundMethod)):
        return f"<builtin {v.name}>"
    if isinstance(v, Module):
        return f"<module {v.name}>"
    if isinstance(v, TaskHandle):
        return f"<task {v.label}{' done' if v.done else ''}>"
    if isinstance(v, ServiceObject):
        return "<service>"
    return str(v)


def to_json_value(v: Any, depth: int = 0) -> Any:
    """Convert to plain JSON data (functions become descriptive strings)."""
    if depth > 64:
        raise ValueError("value nested too deeply")
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, list):
        return [to_json_value(x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {str(k): to_json_value(x, depth + 1) for k, x in v.items()}
    return to_display(v)


def from_host_value(v: Any, depth: int = 0) -> Any:
    """Normalise data returned by host APIs (tuples → lists, other objects → strings)."""
    if depth > 64:
        return None
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [from_host_value(x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {str(k): from_host_value(x, depth + 1) for k, x in v.items()}
    return str(v)
