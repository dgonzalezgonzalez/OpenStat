"""A Stata expression parser.

The do-file language needs Stata's exact operator precedence, its string
syntax and its result references.  A hand written recursive-descent parser
is clearer and safer than rewriting source text into Python syntax: nothing
is ever handed to ``eval``.

Stata precedence, tightest first:

    unary ``+``/``-``/``!``   ->   ``^``   ->   ``*`` ``/``   ->
    ``+`` ``-``   ->   comparison   ->   ``&``   ->   ``|``
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


class ExpressionError(ValueError):
    pass


# ----------------------------------------------------------------------
# Tokens
# ----------------------------------------------------------------------
@dataclass
class Token:
    kind: str
    text: str
    position: int


TOKEN_PATTERN = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<number>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)
  | (?P<string>"[^"\n]*")
  | (?P<name>[A-Za-z_]\w*)
  | (?P<op><=|>=|==|!=|&&|\|\||~=|[-+*/^&|~!<>=(),\[\]:])
    """,
    re.VERBOSE,
)


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    while index < len(text):
        match = TOKEN_PATTERN.match(text, index)
        if not match:
            raise ExpressionError(f"unexpected character {text[index]!r} in expression")
        index = match.end()
        kind = match.lastgroup
        value = match.group()
        if kind == "space":
            continue
        tokens.append(Token(kind, value, match.start()))
    tokens.append(Token("end", "", len(text)))
    return tokens


# ----------------------------------------------------------------------
# AST
# ----------------------------------------------------------------------
@dataclass
class Node:
    pass


@dataclass
class Literal(Node):
    value: object


@dataclass
class StringLiteral(Node):
    value: str


@dataclass
class Name(Node):
    identifier: str


@dataclass
class Unary(Node):
    operator: str
    operand: Node


@dataclass
class Binary(Node):
    operator: str
    left: Node
    right: Node


@dataclass
class Call(Node):
    function: str
    arguments: list = field(default_factory=list)


@dataclass
class Subscript(Node):
    target: Node
    indices: list = field(default_factory=list)


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------
class Parser:
    def __init__(self, text: str):
        self.tokens = tokenize(text)
        self.position = 0

    def peek(self) -> Token:
        return self.tokens[self.position]

    def next(self) -> Token:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def accept(self, text: str) -> bool:
        if self.peek().text == text:
            self.position += 1
            return True
        return False

    def expect(self, text: str):
        if not self.accept(text):
            token = self.peek()
            raise ExpressionError(f"expected {text!r} but found {token.text!r}")

    def parse(self) -> Node:
        node = self.parse_or()
        if self.peek().kind != "end":
            raise ExpressionError(f"unexpected {self.peek().text!r} in expression")
        return node

    def parse_or(self) -> Node:
        node = self.parse_and()
        while self.peek().text == "|":
            self.next()
            node = Binary("|", node, self.parse_and())
        return node

    def parse_and(self) -> Node:
        node = self.parse_comparison()
        while self.peek().text == "&":
            self.next()
            node = Binary("&", node, self.parse_comparison())
        return node

    def parse_comparison(self) -> Node:
        node = self.parse_additive()
        # Stata evaluates chained comparisons left to right: 3 > 2 > 1 is
        # (3 > 2) > 1.
        while self.peek().text in {"==", "!=", "<", ">", "<=", ">="}:
            operator = self.next().text
            node = Binary(operator, node, self.parse_additive())
        return node

    def parse_additive(self) -> Node:
        node = self.parse_multiplicative()
        while self.peek().text in {"+", "-"}:
            operator = self.next().text
            node = Binary(operator, node, self.parse_multiplicative())
        return node

    def parse_multiplicative(self) -> Node:
        node = self.parse_power()
        while self.peek().text in {"*", "/"}:
            operator = self.next().text
            node = Binary(operator, node, self.parse_power())
        return node

    def parse_power(self) -> Node:
        node = self.parse_unary()
        if self.peek().text == "^":
            self.next()
            return Binary("^", node, self.parse_power())
        return node

    def parse_unary(self) -> Node:
        token = self.peek()
        if token.text in {"-", "+", "!", "~"}:
            self.next()
            return Unary(token.text, self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self) -> Node:
        node = self.parse_primary()
        while self.peek().text == "[":
            self.next()
            indices = [self.parse_or()]
            while self.accept(","):
                indices.append(self.parse_or())
            self.expect("]")
            node = Subscript(node, indices)
        return node

    def parse_primary(self) -> Node:
        token = self.next()
        if token.kind == "number":
            return Literal(_number(token.text))
        if token.kind == "string":
            return StringLiteral(token.text[1:-1])
        if token.text == "(":
            node = self.parse_or()
            self.expect(")")
            return node
        if token.kind == "name":
            if self.peek().text == "(":
                self.next()
                arguments = []
                if self.peek().text != ")":
                    arguments.append(self.parse_or())
                    while self.accept(","):
                        arguments.append(self.parse_or())
                self.expect(")")
                return Call(token.text, arguments)
            return Name(token.text)
        raise ExpressionError(f"unexpected {token.text!r} in expression")


def _number(text: str):
    if re.fullmatch(r"\d+", text):
        return int(text)
    return float(text)


def parse_expression(text: str) -> Node:
    return Parser(text).parse()
