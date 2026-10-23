"""Recursive-descent parser for MUSYA.

Grammar (informal)::

    program    := statement* EOF
    statement  := simple NEWLINE | compound
    simple     := app | let | const | assign | print | service | return | break
                | continue | throw | import | emit | assert | expr
    compound   := if | while | for | func | try | on | test
    block      := statement*   (closed by end / elif / else / catch / finally)

    expr       := ternary
    ternary    := or ('?' expr ':' expr)?
    or         := and ('or' and)*
    and        := not ('and' not)*
    not        := 'not' not | compare
    compare    := sum (('=='|'!='|'<'|'>'|'<='|'>='|'in'|'not in') sum)*
    sum        := term (('+'|'-') term)*
    term       := unary (('*'|'/'|'%') unary)*
    unary      := ('-'|'+') unary | 'await' unary | 'spawn' unary | power
    power      := postfix ('**' unary)?
    postfix    := primary ( '(' args ')' | '.' IDENT | '[' expr ']' )*
    primary    := NUMBER | STRING | true | false | null | IDENT | '(' expr ')'
                | list | map | lambda | service-command | 'service'

The colon after block headers (``if x:``) is optional.
"""

from __future__ import annotations

from ..errors import MusyaSyntaxError
from ..lexer import Token, tokenize
from . import ast as A

# Argument "slots" for each service verb. ``word`` means a bare identifier is
# taken literally (``service press Enter``); ``expr`` is any expression
# (``service load url`` reads the variable ``url``). A ``?`` prefix marks the
# slot optional. Verbs not listed accept any number of expressions and are
# validated by the Service Engine itself.
SERVICE_SIGNATURES: dict[str, list[str]] = {
    "start": [], "stop": [], "status": [],
    "load": ["expr"], "open": ["expr"],
    "search": ["expr"], "click": ["expr"], "dblclick": ["expr"], "hover": ["expr"],
    "type": ["expr", "?expr"], "fill": ["expr", "expr"], "select": ["expr", "expr"],
    "press": ["word"],
    "scroll": ["word", "?expr"],
    "wait": ["expr", "?expr"],
    "screenshot": ["?expr"],
    "download": ["expr", "?expr"],
    "event": ["word", "?expr"],
    "ai": ["word", "?expr"],
    "back": [], "forward": [], "reload": [],
    "tab": ["word", "?expr"],
    "exists": ["expr"], "text": ["?expr"], "url": [], "title": [], "html": [], "errors": [],
    "click_at": ["expr", "expr"],
    "install": ["word"],
}

_ARG_TERMINATORS = {":", ")", "]", "}", ",", "?"}
_BLOCK_END = ("end", "elif", "else", "catch", "finally")
_ASSIGN_OPS = ("=", "+=", "-=", "*=", "/=", "%=", "**=")


class Parser:
    def __init__(self, tokens: list[Token], source_name: str = "<script>"):
        self.tokens = tokens
        self.pos = 0
        self.name = source_name
        self.func_depth = 0
        self.loop_depth = 0

    # -- token helpers -------------------------------------------------------
    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, offset: int = 1) -> Token:
        i = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[i]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        if t.type != "EOF":
            self.pos += 1
        return t

    def error(self, msg: str, tok: Token | None = None) -> MusyaSyntaxError:
        t = tok or self.tok
        return MusyaSyntaxError(msg, t.line, t.column, self.name)

    def describe(self, t: Token) -> str:
        if t.type == "EOF":
            return "end of file"
        if t.type == "NEWLINE":
            return "end of line"
        return repr(t.value)

    def expect_op(self, op: str) -> Token:
        if not self.tok.is_op(op):
            raise self.error(f"expected '{op}' but found {self.describe(self.tok)}")
        return self.advance()

    def expect_kw(self, kw: str) -> Token:
        if not self.tok.is_kw(kw):
            raise self.error(f"expected '{kw}' but found {self.describe(self.tok)}")
        return self.advance()

    def expect_ident(self) -> Token:
        if self.tok.type != "IDENT":
            raise self.error(f"expected a name but found {self.describe(self.tok)}")
        return self.advance()

    def skip_newlines(self) -> None:
        while self.tok.type == "NEWLINE":
            self.advance()

    def end_statement(self) -> None:
        if self.tok.type in ("NEWLINE", "EOF"):
            self.skip_newlines()
            return
        if self.tok.is_kw(*_BLOCK_END):
            return  # e.g. "if x: return end" written on one line
        raise self.error(f"unexpected {self.describe(self.tok)} after statement")

    def block_header_end(self) -> None:
        if self.tok.is_op(":"):
            self.advance()
        self.skip_newlines()

    def at(self, t: Token, **extra) -> dict:
        return {"line": t.line, "col": t.column, **extra}

    # -- program / blocks ----------------------------------------------------
    def parse_program(self) -> A.Program:
        self.skip_newlines()
        body = []
        while self.tok.type != "EOF":
            if self.tok.is_kw(*_BLOCK_END):
                raise self.error(f"'{self.tok.value}' without a matching block")
            body.append(self.statement())
        return A.Program(body, source_name=self.name, line=1, col=1)

    def block(self, *terminators: str) -> list:
        body = []
        self.skip_newlines()
        while not self.tok.is_kw(*terminators):
            if self.tok.type == "EOF":
                raise self.error(f"missing '{terminators[0]}' to close block")
            if self.tok.is_kw(*_BLOCK_END):
                raise self.error(f"unexpected '{self.tok.value}'")
            body.append(self.statement())
        return body

    # -- statements ----------------------------------------------------------
    def statement(self):
        t = self.tok
        if t.type == "KEYWORD":
            handler = getattr(self, f"stmt_{t.value}", None)
            if handler is not None:
                return handler()
        return self.stmt_expression()

    def stmt_app(self):
        t = self.advance()
        node = A.AppDecl(self.expression(), **self.at(t))
        self.end_statement()
        return node

    def stmt_let(self, const: bool = False):
        t = self.advance()
        name = self.expect_ident().value
        if self.tok.is_op("="):
            self.advance()
            value = self.expression()
        elif const:
            raise self.error("const needs a value")
        else:
            value = A.Literal(None, **self.at(t))
        self.end_statement()
        return A.Let(name, value, const, **self.at(t))

    def stmt_const(self):
        return self.stmt_let(const=True)

    def stmt_print(self):
        t = self.advance()
        values = []
        if self.tok.type not in ("NEWLINE", "EOF"):
            values.append(self.expression())
            while self.tok.is_op(","):
                self.advance()
                values.append(self.expression())
        self.end_statement()
        return A.Print(values, **self.at(t))

    def stmt_service(self):
        if self.peek().is_op("."):
            return self.stmt_expression()
        t = self.tok
        cmd = self.service_command()
        if self.tok.type == "OP" and self.tok.value in _ASSIGN_OPS:
            raise self.error("cannot assign to a service command")
        self.end_statement()
        return A.ExprStmt(cmd, **self.at(t))

    def stmt_if(self):
        t = self.advance()
        branches = []
        cond = self.expression()
        self.block_header_end()
        body = self.block(*_BLOCK_END)
        branches.append((cond, body))
        otherwise = None
        while True:
            if self.tok.is_kw("elif"):
                self.advance()
                cond = self.expression()
                self.block_header_end()
                branches.append((cond, self.block(*_BLOCK_END)))
            elif self.tok.is_kw("else"):
                self.advance()
                if self.tok.is_kw("if"):  # "else if" sugar
                    self.advance()
                    cond = self.expression()
                    self.block_header_end()
                    branches.append((cond, self.block(*_BLOCK_END)))
                    continue
                self.block_header_end()
                otherwise = self.block("end")
                break
            else:
                break
        self.expect_kw("end")
        self.end_statement()
        return A.If(branches, otherwise, **self.at(t))

    def stmt_while(self):
        t = self.advance()
        cond = self.expression()
        self.block_header_end()
        self.loop_depth += 1
        body = self.block("end")
        self.loop_depth -= 1
        self.expect_kw("end")
        self.end_statement()
        return A.While(cond, body, **self.at(t))

    def stmt_for(self):
        t = self.advance()
        var = self.expect_ident().value
        var2 = None
        if self.tok.is_op(","):
            self.advance()
            var2 = self.expect_ident().value
        self.expect_kw("in")
        iterable = self.expression()
        self.block_header_end()
        self.loop_depth += 1
        body = self.block("end")
        self.loop_depth -= 1
        self.expect_kw("end")
        self.end_statement()
        return A.For(var, var2, iterable, body, **self.at(t))

    def stmt_async(self):
        # "async func name(...)" - every MUSYA function may await, so this is sugar.
        self.advance()
        if not self.tok.is_kw("func"):
            raise self.error("expected 'func' after 'async'")
        return self.stmt_func()

    def stmt_func(self):
        t = self.tok
        if not self.peek().type == "IDENT":
            return self.stmt_expression()
        self.advance()
        name = self.advance().value
        func = self.function_rest(name, t)
        self.end_statement()
        return A.FuncDecl(func, **self.at(t))

    def function_rest(self, name: str | None, t: Token) -> A.FuncExpr:
        params = self.params()
        if self.tok.is_op("=>"):
            self.advance()
            expr = self.expression()
            return A.FuncExpr(name, params, [A.Return(expr, **self.at(t))], **self.at(t))
        self.block_header_end()
        saved_loop = self.loop_depth
        self.loop_depth = 0
        self.func_depth += 1
        body = self.block("end")
        self.func_depth -= 1
        self.loop_depth = saved_loop
        self.expect_kw("end")
        return A.FuncExpr(name, params, body, **self.at(t))

    def params(self) -> list:
        self.expect_op("(")
        params: list[A.Param] = []
        seen = set()
        while not self.tok.is_op(")"):
            pt = self.expect_ident()
            if pt.value in seen:
                raise self.error(f"duplicate parameter '{pt.value}'", pt)
            seen.add(pt.value)
            default = None
            if self.tok.is_op("="):
                self.advance()
                default = self.expression()
            elif params and params[-1].default is not None:
                raise self.error("parameters without defaults must come first", pt)
            params.append(A.Param(pt.value, default, **self.at(pt)))
            if not self.tok.is_op(","):
                break
            self.advance()
        self.expect_op(")")
        return params

    def stmt_return(self):
        t = self.advance()
        if self.func_depth == 0:
            raise self.error("'return' outside of a function", t)
        value = None
        if self.tok.type not in ("NEWLINE", "EOF") and not self.tok.is_kw(*_BLOCK_END):
            value = self.expression()
        self.end_statement()
        return A.Return(value, **self.at(t))

    def stmt_break(self):
        t = self.advance()
        if self.loop_depth == 0:
            raise self.error("'break' outside of a loop", t)
        self.end_statement()
        return A.Break(**self.at(t))

    def stmt_continue(self):
        t = self.advance()
        if self.loop_depth == 0:
            raise self.error("'continue' outside of a loop", t)
        self.end_statement()
        return A.Continue(**self.at(t))

    def stmt_try(self):
        t = self.advance()
        self.block_header_end()
        body = self.block("catch", "finally", "end")
        catch_name = catch_body = finally_body = None
        if self.tok.is_kw("catch"):
            self.advance()
            if self.tok.type == "IDENT":
                catch_name = self.advance().value
            self.block_header_end()
            catch_body = self.block("finally", "end")
        if self.tok.is_kw("finally"):
            self.advance()
            self.block_header_end()
            finally_body = self.block("end")
        if catch_body is None and finally_body is None:
            raise self.error("'try' needs a 'catch' or 'finally' block")
        self.expect_kw("end")
        self.end_statement()
        return A.Try(body, catch_name, catch_body, finally_body, **self.at(t))

    def stmt_throw(self):
        t = self.advance()
        value = self.expression()
        self.end_statement()
        return A.Throw(value, **self.at(t))

    def stmt_import(self):
        t = self.advance()
        if self.tok.type != "STRING":
            raise self.error("import expects a path string, e.g. import \"utils.mysya\"")
        path = self.advance().value
        alias = None
        if self.tok.is_kw("as"):
            self.advance()
            alias = self.expect_ident().value
        self.end_statement()
        return A.Import(path, alias, **self.at(t))

    def event_name(self):
        if self.tok.type == "IDENT" and not self.peek().is_op(".", "["):
            t = self.advance()
            return A.Literal(t.value, **self.at(t))
        if self.tok.type == "STRING":
            t = self.advance()
            return self.string_node(t)
        return self.expression()

    def stmt_on(self):
        t = self.advance()
        event = self.event_name()
        param = None
        if self.tok.is_op("("):
            self.advance()
            param = self.expect_ident().value
            self.expect_op(")")
        self.block_header_end()
        self.func_depth += 1
        body = self.block("end")
        self.func_depth -= 1
        self.expect_kw("end")
        self.end_statement()
        return A.On(event, param, body, **self.at(t))

    def stmt_emit(self):
        t = self.advance()
        event = self.event_name()
        data = None
        if self.tok.is_op(","):
            self.advance()
            data = self.expression()
        self.end_statement()
        return A.Emit(event, data, **self.at(t))

    def stmt_assert(self):
        t = self.advance()
        cond = self.expression()
        message = None
        if self.tok.is_op(","):
            self.advance()
            message = self.expression()
        self.end_statement()
        return A.Assert(cond, message, **self.at(t))

    def stmt_test(self):
        t = self.advance()
        if self.tok.type != "STRING":
            raise self.error("test expects a name string")
        name = self.advance().value
        self.block_header_end()
        self.func_depth += 1
        body = self.block("end")
        self.func_depth -= 1
        self.expect_kw("end")
        self.end_statement()
        return A.TestDecl(name, body, **self.at(t))

    def stmt_expression(self):
        t = self.tok
        expr = self.expression()
        if self.tok.type == "OP" and self.tok.value in _ASSIGN_OPS:
            op_tok = self.advance()
            if not isinstance(expr, (A.Name, A.Member, A.Index)):
                raise self.error("invalid assignment target", op_tok)
            value = self.expression()
            self.end_statement()
            return A.Assign(expr, op_tok.value, value, **self.at(t))
        self.end_statement()
        return A.ExprStmt(expr, **self.at(t))

    # -- expressions -----------------------------------------------------------
    def expression(self):
        return self.ternary()

    def ternary(self):
        cond = self.logic_or()
        if self.tok.is_op("?"):
            t = self.advance()
            then = self.expression()
            self.expect_op(":")
            otherwise = self.expression()
            return A.Ternary(cond, then, otherwise, **self.at(t))
        return cond

    def logic_or(self):
        left = self.logic_and()
        while self.tok.is_kw("or"):
            t = self.advance()
            left = A.Logical("or", left, self.logic_and(), **self.at(t))
        return left

    def logic_and(self):
        left = self.logic_not()
        while self.tok.is_kw("and"):
            t = self.advance()
            left = A.Logical("and", left, self.logic_not(), **self.at(t))
        return left

    def logic_not(self):
        if self.tok.is_kw("not"):
            t = self.advance()
            return A.Unary("not", self.logic_not(), **self.at(t))
        return self.comparison()

    def comparison(self):
        left = self.sum()
        while True:
            t = self.tok
            if t.type == "OP" and t.value in ("==", "!=", "<", ">", "<=", ">="):
                self.advance()
                left = A.Binary(t.value, left, self.sum(), **self.at(t))
            elif t.is_kw("in"):
                self.advance()
                left = A.Binary("in", left, self.sum(), **self.at(t))
            elif t.is_kw("not") and self.peek().is_kw("in"):
                self.advance()
                self.advance()
                left = A.Binary("not in", left, self.sum(), **self.at(t))
            else:
                return left

    def sum(self):
        left = self.term()
        while self.tok.is_op("+", "-"):
            t = self.advance()
            left = A.Binary(t.value, left, self.term(), **self.at(t))
        return left

    def term(self):
        left = self.unary()
        while self.tok.is_op("*", "/", "%"):
            t = self.advance()
            left = A.Binary(t.value, left, self.unary(), **self.at(t))
        return left

    def unary(self):
        t = self.tok
        if t.is_op("-", "+"):
            self.advance()
            return A.Unary(t.value, self.unary(), **self.at(t))
        if t.is_kw("await"):
            self.advance()
            return A.Await(self.unary(), **self.at(t))
        if t.is_kw("spawn"):
            self.advance()
            call = self.unary()
            if not isinstance(call, (A.Call, A.ServiceCommand)):
                raise self.error("spawn expects a function call or service command", t)
            return A.Spawn(call, **self.at(t))
        return self.power()

    def power(self):
        base = self.postfix()
        if self.tok.is_op("**"):
            t = self.advance()
            return A.Binary("**", base, self.unary(), **self.at(t))
        return base

    def postfix(self):
        expr = self.primary()
        while True:
            t = self.tok
            if t.is_op("("):
                self.advance()
                args = []
                while not self.tok.is_op(")"):
                    args.append(self.expression())
                    if not self.tok.is_op(","):
                        break
                    self.advance()
                self.expect_op(")")
                expr = A.Call(expr, args, **self.at(t))
            elif t.is_op("."):
                self.advance()
                nt = self.tok
                if nt.type not in ("IDENT", "KEYWORD"):
                    raise self.error("expected a property name after '.'")
                self.advance()
                expr = A.Member(expr, nt.value, **self.at(nt))
            elif t.is_op("["):
                self.advance()
                index = self.expression()
                self.expect_op("]")
                expr = A.Index(expr, index, **self.at(t))
            else:
                return expr

    def primary(self):
        t = self.tok
        if t.type == "NUMBER":
            self.advance()
            return A.Literal(t.value, **self.at(t))
        if t.type == "STRING":
            self.advance()
            return self.string_node(t)
        if t.type == "IDENT":
            self.advance()
            return A.Name(t.value, **self.at(t))
        if t.is_kw("true", "false", "null"):
            self.advance()
            return A.Literal({"true": True, "false": False, "null": None}[t.value], **self.at(t))
        if t.is_kw("service"):
            if self.peek().is_op("."):
                self.advance()
                return A.ServiceRef(**self.at(t))
            return self.service_command()
        if t.is_kw("func"):
            self.advance()
            return self.function_rest(None, t)
        if t.is_op("("):
            self.advance()
            expr = self.expression()
            self.expect_op(")")
            return expr
        if t.is_op("["):
            self.advance()
            items = []
            while not self.tok.is_op("]"):
                items.append(self.expression())
                if not self.tok.is_op(","):
                    break
                self.advance()
            self.expect_op("]")
            return A.ListExpr(items, **self.at(t))
        if t.is_op("{"):
            self.advance()
            pairs = []
            while not self.tok.is_op("}"):
                kt = self.tok
                if kt.type in ("IDENT", "KEYWORD"):
                    self.advance()
                    key = A.Literal(kt.value, **self.at(kt))
                elif kt.type in ("STRING", "NUMBER"):
                    self.advance()
                    key = A.Literal(kt.value, **self.at(kt))
                else:
                    raise self.error("map keys must be names, strings or numbers")
                self.expect_op(":")
                pairs.append((key, self.expression()))
                if not self.tok.is_op(","):
                    break
                self.advance()
            self.expect_op("}")
            return A.MapExpr(pairs, **self.at(t))
        raise self.error(f"unexpected {self.describe(t)}")

    def service_command(self) -> A.ServiceCommand:
        t = self.advance()  # 'service'
        vt = self.tok
        if vt.type not in ("IDENT", "KEYWORD"):
            raise self.error("expected a service command, e.g. 'service load \"https://example.com\"'")
        self.advance()
        verb = vt.value
        args: list = []
        sig = SERVICE_SIGNATURES.get(verb)
        if sig is None:
            while not self.at_arg_end():
                args.append(self.expression())
                if self.tok.is_op(","):
                    self.advance()
        else:
            for slot in sig:
                optional = slot.startswith("?")
                kind = slot.lstrip("?")
                if self.at_arg_end():
                    if optional:
                        break
                    raise self.error(f"'service {verb}' is missing an argument")
                if kind == "word" and self.tok.type in ("IDENT", "KEYWORD") and not self.peek().is_op("(", ".", "["):
                    wt = self.advance()
                    args.append(A.Literal(wt.value, **self.at(wt)))
                else:
                    args.append(self.expression())
                if self.tok.is_op(","):
                    self.advance()
            if not self.at_arg_end():
                raise self.error(f"too many arguments for 'service {verb}'")
        return A.ServiceCommand(verb, args, **self.at(t))

    def at_arg_end(self) -> bool:
        t = self.tok
        return t.type in ("NEWLINE", "EOF") or (t.type == "OP" and t.value in _ARG_TERMINATORS) \
            or t.is_kw(*_BLOCK_END, "and", "or")

    # -- template strings --------------------------------------------------------
    def string_node(self, t: Token):
        raw: str = t.value
        if "${" not in raw:
            return A.Literal(raw.replace("\x00$", "$"), **self.at(t))
        parts: list = []
        i = 0
        buf = []
        while i < len(raw):
            if raw.startswith("\x00$", i):
                buf.append("$")
                i += 2
            elif raw.startswith("${", i):
                depth = 1
                j = i + 2
                while j < len(raw) and depth:
                    if raw[j] == "{":
                        depth += 1
                    elif raw[j] == "}":
                        depth -= 1
                    j += 1
                if depth:
                    raise self.error("unclosed '${' in string", t)
                code = raw[i + 2:j - 1]
                if buf:
                    parts.append("".join(buf))
                    buf = []
                sub_tokens = tokenize(code, self.name)
                # re-anchor positions to the string token for nicer errors
                sub_tokens = [Token(x.type, x.value, t.line, t.column) for x in sub_tokens]
                sub = Parser(sub_tokens, self.name)
                sub.skip_newlines()
                expr = sub.expression()
                sub.skip_newlines()
                if sub.tok.type != "EOF":
                    raise self.error("invalid expression inside '${...}'", t)
                parts.append(expr)
                i = j
            else:
                buf.append(raw[i])
                i += 1
        if buf:
            parts.append("".join(buf))
        return A.TemplateString(parts, **self.at(t))


def parse(source: str, source_name: str = "<script>") -> A.Program:
    return Parser(tokenize(source, source_name), source_name).parse_program()


__all__ = ["parse", "Parser", "SERVICE_SIGNATURES"]
