"""AgentWire-Cue v1.4.3 expression engine.

Implements v1.3 §5 + v1.2 spec.md §3 grammar and type rules.

- 30-line recursive-descent parser (per spec; actual is ~80 lines for
  clarity on tokenize + parse + eval, with the spec intent preserved)
- Strict no-implicit-conversion comparison (v1.2 spec §3.2)
- Whitelist namespaces: event / context / state / meta / peers / history (v1.2 spec §2.2)
- Functions: now / since / duration_in_state (v1.2 spec §3.3)
- 6 namespaces only: secrets / env are explicitly denied
"""
from __future__ import annotations

import re
from typing import Any

_TOKEN_PATTERN = re.compile(
    r"(?u)"
    r"-\d+\.\d+|-\d+|\d+\.\d+|\d+|"  # number: negative float, negative int, float, int
    r'"[^"]*"|'                # double-quoted string
    r"'[^']*'|"                # single-quoted string
    r"==|!=|>=|<=|&&|\|\||!|>|<|"  # multi-char ops
    r"\(|\)|,|\.|\$|"          # punctuation
    r"[\w]+"                    # identifier (unicode-aware: supports e.g. 初梦 / _foo / abc123)
)


class ExpressionError(Exception):
    """Raised on parse error, type error, or forbidden namespace."""


class _Parser:
    """Recursive-descent parser. Produces nested-dict AST."""

    def __init__(self, tokens: list[str], source: str = "") -> None:
        self.tokens = tokens
        self.pos = 0
        self.source = source

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def eat(self, expected: str) -> str:
        tok = self.peek()
        if tok != expected:
            raise ExpressionError(
                f"expected {expected!r} at token {self.pos}, got {tok!r}"
            )
        self.pos += 1
        return tok

    def parse_expr(self) -> dict:
        node = self.parse_or()
        if self.pos < len(self.tokens):
            raise ExpressionError(
                f"unexpected trailing token at pos {self.pos}: {self.tokens[self.pos]!r}"
            )
        return node

    def parse_or(self) -> dict:
        lhs = self.parse_and()
        while self.peek() == "||":
            self.pos += 1
            rhs = self.parse_and()
            lhs = {"op": "logical", "logical_op": "or", "lhs": lhs, "rhs": rhs}
        return lhs

    def parse_and(self) -> dict:
        lhs = self.parse_not()
        while self.peek() == "&&":
            self.pos += 1
            rhs = self.parse_not()
            lhs = {"op": "logical", "logical_op": "and", "lhs": lhs, "rhs": rhs}
        return lhs

    def parse_not(self) -> dict:
        if self.peek() == "!":
            self.pos += 1
            return {"op": "not", "expr": self.parse_not()}
        return self.parse_cmp()

    def parse_cmp(self) -> dict:
        lhs = self.parse_primary()
        tok = self.peek()
        if tok in ("==", "!=", ">", "<", ">=", "<="):
            self.pos += 1
            rhs = self.parse_primary()
            return {"op": "cmp", "cmp_op": tok, "lhs": lhs, "rhs": rhs}
        return lhs

    def parse_primary(self) -> dict:
        tok = self.peek()
        if tok is None:
            raise ExpressionError("unexpected end of expression")
        if tok == "(":
            self.pos += 1
            node = self.parse_or()
            self.eat(")")
            return node
        if tok == "$" or re.match(r"^[A-Za-z_]", tok or ""):
            return self.parse_variable_or_call()
        return self.parse_literal()

    def parse_variable_or_call(self) -> dict:
        first = self.peek()
        assert first is not None
        if first in ("true", "false", "null"):
            return self.parse_literal()
        name = self.eat(first)
        if self.peek() == "(":
            self.pos += 1
            args: list[dict] = []
            if self.peek() != ")":
                args.append(self.parse_or())
                while self.peek() == ",":
                    self.pos += 1
                    args.append(self.parse_or())
            self.eat(")")
            return {"op": "function", "name": name, "args": args}
        # variable path
        path = [name]
        while self.peek() == ".":
            self.pos += 1
            nxt = self.peek()
            if nxt is None or not re.match(r"(?u)^\w", nxt or ""):
                raise ExpressionError(f"expected identifier after '.', got {nxt!r}")
            path.append(self.eat(nxt))
        # v1.4.3: method call on path result (e.g. peers.Pawly.history.last(5))
        if self.peek() == "(":
            self.pos += 1
            args: list[dict] = []
            if self.peek() != ")":
                args.append(self.parse_or())
                while self.peek() == ",":
                    self.pos += 1
                    args.append(self.parse_or())
            self.eat(")")
            return {"op": "method", "path": path, "args": args}
        return {"op": "variable", "path": path}

    def parse_literal(self) -> dict:
        tok = self.eat(self.peek())  # type: ignore[arg-type]
        if tok in ("true", "false"):
            return {"op": "literal", "value": tok == "true"}
        if tok == "null":
            return {"op": "literal", "value": None}
        if re.match(r"^-?\d+\.\d+$", tok):
            return {"op": "literal", "value": float(tok)}
        if re.match(r"^-?\d+$", tok):
            return {"op": "literal", "value": int(tok)}
        if (tok.startswith('"') and tok.endswith('"')) or (
            tok.startswith("'") and tok.endswith("'")
        ):
            return {"op": "literal", "value": tok[1:-1]}
        raise ExpressionError(f"unrecognized literal: {tok!r}")


def tokenize(expr: str) -> list[str]:
    """Lex into tokens. Raises ExpressionError on stray characters."""
    tokens: list[str] = []
    pos = 0
    n = len(expr)
    while pos < n:
        if expr[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_PATTERN.match(expr, pos)
        if not m:
            raise ExpressionError(
                f"unrecognized token at position {pos}: {expr[pos:pos+10]!r}"
            )
        tokens.append(m.group(0))
        pos = m.end()
    return tokens


def parse(expr: str) -> dict:
    """Parse expression string into nested-dict AST."""
    tokens = tokenize(expr)
    return _Parser(tokens, source=expr).parse_expr()


_ALLOWED_NAMESPACES = frozenset({
    "event", "context", "state", "meta", "now",
    # v1.4.3: history and peer namespaces
    "peers", "history",
})


def _resolve_path(env: dict, path: list[str]) -> Any:
    """Resolve a dotted path against env. Raises ExpressionError on bad ns."""
    if not path:
        raise ExpressionError("empty variable path")
    ns = path[0]
    if ns not in _ALLOWED_NAMESPACES:
        raise ExpressionError(
            f"namespace {ns!r} not in whitelist "
            f"(allowed: {sorted(_ALLOWED_NAMESPACES)})"
        )
    if ns == "now":
        if len(path) != 1:
            raise ExpressionError("'now' is a function, not a namespace")
        return env["now"]
    cur: Any = env.get(ns, {})
    for key in path[1:]:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            # v1.4.3: support object proxies (e.g. _PeersNamespace, _PeerProxy)
            cur = getattr(cur, key, None)
        if cur is None:
            return None
    return cur


def _call_function(name: str, arg_asts: list[dict], env: dict) -> Any:
    if name == "now":
        if arg_asts:
            raise ExpressionError("now() takes no arguments")
        return env["now"]
    if name == "since":
        if len(arg_asts) != 1:
            raise ExpressionError("since() takes exactly 1 argument")
        ts = evaluate(arg_asts[0], env)
        if not isinstance(ts, (int, float)):
            raise ExpressionError(f"since() requires numeric arg, got {type(ts).__name__}")
        return env["now"] - int(ts)
    if name == "duration_in_state":
        if arg_asts:
            raise ExpressionError("duration_in_state() takes no arguments")
        return env.get("state", {}).get("duration_ms", 0)
    raise ExpressionError(f"unknown function: {name}")


def _compare(lhs: Any, op: str, rhs: Any) -> bool:
    """Strict, no-implicit-conversion comparison (v1.2 spec §3.2)."""
    if op == "==":
        if lhs is None or rhs is None:
            return lhs is None and rhs is None
        if isinstance(lhs, bool) or isinstance(rhs, bool):
            return type(lhs) is bool and type(rhs) is bool and lhs == rhs
        if isinstance(lhs, str) and isinstance(rhs, str):
            return lhs == rhs
        if isinstance(lhs, int) and isinstance(rhs, int):
            return lhs == rhs
        if isinstance(lhs, (int, float)) and isinstance(rhs, (int, float)):
            return float(lhs) == float(rhs)
        return False
    if op == "!=":
        return not _compare(lhs, "==", rhs)
    # ordered comparison: numeric only
    if isinstance(lhs, bool) or isinstance(rhs, bool):
        return False
    if not isinstance(lhs, (int, float)) or not isinstance(rhs, (int, float)):
        return False
    if op == ">":
        return float(lhs) > float(rhs)
    if op == "<":
        return float(lhs) < float(rhs)
    if op == ">=":
        return float(lhs) >= float(rhs)
    if op == "<=":
        return float(lhs) <= float(rhs)
    return False


def evaluate(ast: dict, env: dict) -> Any:
    """Recursive eval over AST. Returns bool (for guard) or Any (for templates)."""
    op = ast["op"]

    if op == "literal":
        return ast["value"]

    if op == "variable":
        return _resolve_path(env, ast["path"])

    if op == "function":
        return _call_function(ast["name"], ast["args"], env)

    if op == "method":
        # v1.4.3: a.b.c(args) — resolve path to object, then call its method
        if not ast["path"]:
            raise ExpressionError("empty method path")
        ns = ast["path"][0]
        if ns not in _ALLOWED_NAMESPACES:
            raise ExpressionError(
                f"namespace {ns!r} not in whitelist "
                f"(allowed: {sorted(_ALLOWED_NAMESPACES)})"
            )
        obj = _resolve_path(env, ast["path"][:-1])
        method_name = ast["path"][-1]
        if obj is None:
            return None
        method = getattr(obj, method_name, None)
        if method is None or not callable(method):
            return None
        args = [evaluate(a, env) for a in ast["args"]]
        try:
            return method(*args)
        except Exception:
            return None

    if op == "cmp":
        lhs = evaluate(ast["lhs"], env)
        rhs = evaluate(ast["rhs"], env)
        return _compare(lhs, ast["cmp_op"], rhs)

    if op == "logical":
        # S5.1 fix (carried from spec review): use logical_op, not op.
        # outer 'op' is 'logical'; nested 'op' would be wrong (it's 'literal' etc.)
        if ast["logical_op"] == "and":
            return evaluate(ast["lhs"], env) and evaluate(ast["rhs"], env)
        return evaluate(ast["lhs"], env) or evaluate(ast["rhs"], env)

    if op == "not":
        return not evaluate(ast["expr"], env)

    raise ExpressionError(f"unknown AST op: {op!r}")


_TEMPLATE_PATTERN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def render_template(template: str, env: dict) -> str:
    """Replace {{X}} with env-resolved str value (v1.2 spec §2.3)."""

    def replacer(match: re.Match[str]) -> str:
        path_str = match.group(1)
        # paths may be dotted: event.x.y
        path = path_str.split(".")
        try:
            value = _resolve_path(env, path)
        except ExpressionError:
            raise
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    return _TEMPLATE_PATTERN.sub(replacer, template)
