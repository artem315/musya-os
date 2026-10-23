"""MUSYA lexer: turns ``.mysya`` source text into a flat list of tokens.

Design notes
------------
* Indentation is **not** significant: blocks are closed with ``end``.
* Statements are separated by newlines (or ``;``). Newlines inside ``()``,
  ``[]`` and ``{}`` are ignored so long expressions can span lines.
* Comments start with ``#`` or ``//`` and run to the end of the line.
* Strings use ``"..."`` / ``'...'`` or triple quotes for multi-line text.
  ``${expr}`` inside a string is interpolation; the lexer keeps the raw text
  and the parser splits it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import MusyaSyntaxError

KEYWORDS = frozenset({
    "app", "let", "const", "if", "elif", "else", "end", "while", "for", "in",
    "func", "return", "break", "continue", "true", "false", "null", "and", "or",
    "not", "service", "print", "try", "catch", "finally", "throw", "import",
    "as", "on", "emit", "spawn", "await", "assert", "test", "async",
})

# Longest operators first so that maximal munch works.
OPERATORS = (
    "**=", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "**", "=>",
    "+", "-", "*", "/", "%", "<", ">", "=", "(", ")", "[", "]", "{", "}",
    ",", ":", ".", ";", "?",
)

OPENERS = {"(": ")", "[": "]", "{": "}"}
CLOSERS = {")", "]", "}"}


@dataclass(frozen=True)
class Token:
    type: str  # NUMBER, STRING, IDENT, KEYWORD, OP, NEWLINE, EOF
    value: object
    line: int
    column: int

    def is_op(self, *ops: str) -> bool:
        return self.type == "OP" and self.value in ops

    def is_kw(self, *kws: str) -> bool:
        return self.type == "KEYWORD" and self.value in kws

    def __repr__(self) -> str:  # pragma: no cover
        return f"Token({self.type}, {self.value!r}, {self.line}:{self.column})"


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'"}


class Lexer:
    def __init__(self, source: str, source_name: str = "<script>"):
        # Normalise newlines and strip a UTF-8 BOM if present.
        self.src = source.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
        self.name = source_name
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []
        self.depth: list[str] = []

    # -- helpers -----------------------------------------------------------
    def error(self, msg: str, line: int | None = None, col: int | None = None) -> MusyaSyntaxError:
        return MusyaSyntaxError(msg, line or self.line, col or self.col, self.name)

    def peek(self, offset: int = 0) -> str:
        i = self.pos + offset
        return self.src[i] if i < len(self.src) else ""

    def advance(self, n: int = 1) -> str:
        out = self.src[self.pos:self.pos + n]
        for ch in out:
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
        self.pos += n
        return out

    def add(self, type_: str, value, line: int, col: int) -> None:
        self.tokens.append(Token(type_, value, line, col))

    def add_newline(self, line: int, col: int) -> None:
        # Collapse runs of newlines and never start the stream with one.
        if self.tokens and self.tokens[-1].type != "NEWLINE":
            self.add("NEWLINE", "\n", line, col)

    # -- main loop ---------------------------------------------------------
    def tokenize(self) -> list[Token]:
        while self.pos < len(self.src):
            ch = self.peek()
            line, col = self.line, self.col

            if ch in " \t\f\v":
                self.advance()
            elif ch == "\\" and self.peek(1) == "\n":
                self.advance(2)  # explicit line continuation
            elif ch == "\n":
                self.advance()
                if not self.depth:
                    self.add_newline(line, col)
            elif ch == "#" or (ch == "/" and self.peek(1) == "/"):
                while self.pos < len(self.src) and self.peek() != "\n":
                    self.advance()
            elif ch.isdigit() or (ch == "." and self.peek(1).isdigit()):
                self.lex_number(line, col)
            elif ch in "\"'":
                self.lex_string(line, col)
            elif ch.isalpha() or ch == "_":
                self.lex_ident(line, col)
            else:
                self.lex_operator(line, col)

        if self.depth:
            raise self.error(f"unclosed '{self.depth[-1]}' at end of file")
        self.add_newline(self.line, self.col)
        self.add("EOF", None, self.line, self.col)
        return self.tokens

    def lex_number(self, line: int, col: int) -> None:
        start = self.pos
        seen_dot = False
        seen_exp = False
        while True:
            ch = self.peek()
            if ch.isdigit() or ch == "_":
                self.advance()
            elif ch == "." and not seen_dot and not seen_exp and self.peek(1).isdigit():
                seen_dot = True
                self.advance()
            elif ch in "eE" and not seen_exp and (self.peek(1).isdigit() or
                                                   (self.peek(1) in "+-" and self.peek(2).isdigit())):
                seen_exp = True
                self.advance(2 if self.peek(1) in "+-" else 1)
            else:
                break
        text = self.src[start:self.pos].replace("_", "")
        if self.peek().isalpha() or self.peek() == "_":
            raise self.error(f"invalid number literal '{self.src[start:self.pos + 1]}'", line, col)
        value = float(text) if (seen_dot or seen_exp) else int(text)
        self.add("NUMBER", value, line, col)

    def lex_string(self, line: int, col: int) -> None:
        quote = self.peek()
        triple = self.src.startswith(quote * 3, self.pos)
        self.advance(3 if triple else 1)
        chunks: list[str] = []
        while True:
            if self.pos >= len(self.src):
                raise self.error("unterminated string", line, col)
            ch = self.peek()
            if triple and self.src.startswith(quote * 3, self.pos):
                self.advance(3)
                break
            if not triple and ch == quote:
                self.advance()
                break
            if not triple and ch == "\n":
                raise self.error("unterminated string (use triple quotes for multi-line text)", line, col)
            if ch == "\\":
                nxt = self.peek(1)
                if nxt in _ESCAPES:
                    chunks.append(_ESCAPES[nxt])
                    self.advance(2)
                elif nxt == "$":
                    # Keep an escaped dollar distinguishable from interpolation.
                    chunks.append("\x00$")
                    self.advance(2)
                elif nxt == "u" and self.peek(2) == "{":
                    end = self.src.find("}", self.pos + 3)
                    if end == -1:
                        raise self.error("bad unicode escape")
                    code = self.src[self.pos + 3:end]
                    try:
                        chunks.append(chr(int(code, 16)))
                    except ValueError:
                        raise self.error(f"bad unicode escape '\\u{{{code}}}'") from None
                    self.advance(end - self.pos + 1)
                elif nxt == "\n":
                    self.advance(2)
                else:
                    raise self.error(f"unknown escape '\\{nxt}'")
            else:
                chunks.append(ch)
                self.advance()
        self.add("STRING", "".join(chunks), line, col)

    def lex_ident(self, line: int, col: int) -> None:
        start = self.pos
        while self.peek().isalnum() or self.peek() == "_":
            self.advance()
        word = self.src[start:self.pos]
        self.add("KEYWORD" if word in KEYWORDS else "IDENT", word, line, col)

    def lex_operator(self, line: int, col: int) -> None:
        for op in OPERATORS:
            if self.src.startswith(op, self.pos):
                self.advance(len(op))
                if op in OPENERS:
                    self.depth.append(op)
                elif op in CLOSERS:
                    if not self.depth or OPENERS[self.depth[-1]] != op:
                        raise self.error(f"unexpected '{op}'", line, col)
                    self.depth.pop()
                if op == ";":
                    if not self.depth:
                        self.add_newline(line, col)
                    return
                self.add("OP", op, line, col)
                return
        raise self.error(f"unexpected character {self.peek()!r}", line, col)


def tokenize(source: str, source_name: str = "<script>") -> list[Token]:
    return Lexer(source, source_name).tokenize()


__all__ = ["Token", "Lexer", "tokenize", "KEYWORDS"]
