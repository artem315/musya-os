"""AST node definitions for MUSYA.

Every node carries ``line``/``col`` so runtime errors can point at source.
Nodes are plain dataclasses and can be serialised with :func:`to_dict`
(used by Service Studio's "Debug → AST" view).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any


@dataclass
class Node:
    line: int = field(default=0, kw_only=True)
    col: int = field(default=0, kw_only=True)


# --- expressions -----------------------------------------------------------

@dataclass
class Literal(Node):
    value: Any


@dataclass
class TemplateString(Node):
    parts: list  # list[str | Node]


@dataclass
class Name(Node):
    id: str


@dataclass
class ListExpr(Node):
    items: list


@dataclass
class MapExpr(Node):
    pairs: list  # list[tuple[Node, Node]]


@dataclass
class Unary(Node):
    op: str
    operand: Node


@dataclass
class Binary(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Logical(Node):
    op: str  # and / or
    left: Node
    right: Node


@dataclass
class Ternary(Node):
    cond: Node
    then: Node
    otherwise: Node


@dataclass
class Call(Node):
    callee: Node
    args: list


@dataclass
class Member(Node):
    obj: Node
    name: str


@dataclass
class Index(Node):
    obj: Node
    index: Node


@dataclass
class FuncExpr(Node):
    name: str | None
    params: list  # list[Param]
    body: list


@dataclass
class Param(Node):
    name: str
    default: Node | None = None


@dataclass
class Await(Node):
    expr: Node


@dataclass
class Spawn(Node):
    call: Node


@dataclass
class ServiceCommand(Node):
    verb: str
    args: list
    sub: str | None = None  # e.g. "ai" -> sub "start"


@dataclass
class ServiceRef(Node):
    """The bare ``service`` object used in ``service.exists(...)``."""


# --- statements --------------------------------------------------------------

@dataclass
class Program(Node):
    body: list
    source_name: str = "<script>"


@dataclass
class AppDecl(Node):
    name: Node


@dataclass
class Let(Node):
    name: str
    value: Node
    const: bool = False


@dataclass
class Assign(Node):
    target: Node  # Name | Member | Index
    op: str  # "=", "+=", ...
    value: Node


@dataclass
class ExprStmt(Node):
    expr: Node


@dataclass
class Print(Node):
    values: list


@dataclass
class If(Node):
    branches: list  # list[tuple[cond, body]]
    otherwise: list | None


@dataclass
class While(Node):
    cond: Node
    body: list


@dataclass
class For(Node):
    var: str
    var2: str | None
    iterable: Node
    body: list


@dataclass
class FuncDecl(Node):
    func: FuncExpr


@dataclass
class Return(Node):
    value: Node | None


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class Try(Node):
    body: list
    catch_name: str | None
    catch_body: list | None
    finally_body: list | None


@dataclass
class Throw(Node):
    value: Node


@dataclass
class Import(Node):
    path: str
    alias: str | None


@dataclass
class On(Node):
    event: Node
    param: str | None
    body: list


@dataclass
class Emit(Node):
    event: Node
    data: Node | None


@dataclass
class Assert(Node):
    cond: Node
    message: Node | None


@dataclass
class TestDecl(Node):
    name: str
    body: list


def to_dict(node: Any) -> Any:
    """Serialise an AST to JSON-compatible data."""
    if is_dataclass(node):
        out = {"node": type(node).__name__}
        for f in fields(node):
            out[f.name] = to_dict(getattr(node, f.name))
        return out
    if isinstance(node, (list, tuple)):
        return [to_dict(x) for x in node]
    return node
