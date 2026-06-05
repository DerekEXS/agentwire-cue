"""Test suite for expression engine — v1.3 §5 + v1.2 spec.md §3."""
from __future__ import annotations

import pytest

from agentwire_cue.core.expression import (
    ExpressionError,
    _compare,
    evaluate,
    parse,
    render_template,
    tokenize,
)


# ---------- tokenize ----------

class TestTokenize:
    def test_numbers_int_and_float(self):
        assert tokenize("1 2.5 -3 -0.5") == ["1", "2.5", "-3", "-0.5"]

    def test_strings(self):
        assert tokenize('"hello" \'world\'') == ['"hello"', "'world'"]

    def test_operators(self):
        assert tokenize("== != >= <= && ||") == ["==", "!=", ">=", "<=", "&&", "||"]

    def test_unrecognized_raises(self):
        with pytest.raises(ExpressionError, match="unrecognized"):
            tokenize("a @ b")


# ---------- parse ----------

class TestParse:
    def test_literal_int(self):
        ast = parse("42")
        assert ast == {"op": "literal", "value": 42}

    def test_literal_string(self):
        ast = parse('"hi"')
        assert ast == {"op": "literal", "value": "hi"}

    def test_literal_bool(self):
        assert parse("true")["value"] is True
        assert parse("false")["value"] is False

    def test_literal_null(self):
        assert parse("null")["value"] is None

    def test_variable(self):
        ast = parse("event.message.text")
        assert ast == {"op": "variable", "path": ["event", "message", "text"]}

    def test_function_call_no_args(self):
        ast = parse("now()")
        assert ast == {"op": "function", "name": "now", "args": []}

    def test_function_call_with_args(self):
        ast = parse("since(event.t)")
        assert ast == {"op": "function", "name": "since", "args": [
            {"op": "variable", "path": ["event", "t"]}
        ]}

    def test_comparison(self):
        ast = parse("context.x > 5")
        assert ast["op"] == "cmp"
        assert ast["cmp_op"] == ">"
        assert ast["lhs"]["path"] == ["context", "x"]
        assert ast["rhs"]["value"] == 5

    def test_logical_and(self):
        ast = parse("a && b")
        assert ast == {
            "op": "logical", "logical_op": "and",
            "lhs": {"op": "variable", "path": ["a"]},
            "rhs": {"op": "variable", "path": ["b"]},
        }

    def test_logical_or(self):
        ast = parse("a || b")
        assert ast["logical_op"] == "or"

    def test_not(self):
        ast = parse("!a")
        assert ast == {"op": "not", "expr": {"op": "variable", "path": ["a"]}}

    def test_parens(self):
        ast = parse("(a || b) && c")
        assert ast["op"] == "logical"
        assert ast["logical_op"] == "and"
        assert ast["lhs"]["op"] == "logical" and ast["lhs"]["logical_op"] == "or"

    def test_precedence_and_over_or(self):
        # a || b && c => a || (b && c)
        ast = parse("a || b && c")
        assert ast["op"] == "logical" and ast["logical_op"] == "or"
        assert ast["rhs"]["logical_op"] == "and"

    def test_unexpected_trailing_raises(self):
        with pytest.raises(ExpressionError, match="trailing"):
            parse("a b")


# ---------- _compare (type rules from v1.2 spec §3.2) ----------

class TestCompare:
    def test_int_eq_int(self):
        assert _compare(1, "==", 1) is True
        assert _compare(1, "==", 2) is False

    def test_str_eq_str(self):
        assert _compare("a", "==", "a") is True
        assert _compare("a", "==", "b") is False

    def test_bool_eq_int_is_false(self):
        # v1.2 spec §3.2: bool and int never equal
        assert _compare(True, "==", 1) is False
        assert _compare(False, "==", 0) is False

    def test_str_eq_int_is_false(self):
        assert _compare("5", "==", 5) is False

    def test_int_and_float_compare(self):
        assert _compare(1, "==", 1.0) is True
        assert _compare(1, "<", 2.0) is True
        assert _compare(2, ">", 1.5) is True

    def test_null_eq_null(self):
        assert _compare(None, "==", None) is True
        assert _compare(None, "==", 0) is False
        assert _compare(None, "!=", 0) is True

    def test_bool_ordered_compare_always_false(self):
        # v1.2 spec §3.2: bool and int/float: never equal, ordered also false
        assert _compare(True, ">", 0) is False
        assert _compare(True, "<", False) is False

    def test_ne_is_negation_of_eq(self):
        assert _compare(1, "!=", 2) is True
        assert _compare(1, "!=", 1) is False


# ---------- evaluate ----------

class TestEvaluate:
    def test_literal(self):
        assert evaluate(parse("42"), {}) == 42

    def test_variable_resolves(self):
        ast = parse("context.x")
        assert evaluate(ast, {"context": {"x": 7}}) == 7

    def test_variable_dotted(self):
        ast = parse("event.message.text")
        assert evaluate(ast, {"event": {"message": {"text": "hi"}}}) == "hi"

    def test_forbidden_namespace_raises(self):
        with pytest.raises(ExpressionError, match="not in whitelist"):
            evaluate(parse("secrets.token"), {"secrets": {"token": "x"}})

    def test_env_namespace_raises(self):
        with pytest.raises(ExpressionError, match="not in whitelist"):
            evaluate(parse("env.HOME"), {"env": {"HOME": "/x"}})

    def test_now_function(self):
        env = {"now": 1000, "context": {}, "event": {}, "state": {}, "meta": {}}
        assert evaluate(parse("now()"), env) == 1000

    def test_since_function(self):
        env = {"now": 1000, "event": {"t": 700}, "context": {}, "state": {}, "meta": {}}
        assert evaluate(parse("since(event.t)"), env) == 300

    def test_duration_in_state(self):
        env = {"now": 1000, "state": {"duration_ms": 50}, "context": {}, "event": {}, "meta": {}}
        assert evaluate(parse("duration_in_state()"), env) == 50

    def test_unknown_function_raises(self):
        env = {"now": 0}
        with pytest.raises(ExpressionError, match="unknown function"):
            evaluate(parse("eval('x')"), env)

    def test_guard_compound(self):
        env = {"now": 0, "context": {"n": 6}, "state": {"duration_ms": 100}, "event": {}, "meta": {}}
        assert evaluate(parse("context.n > 5 && state.duration_ms < 60000"), env) is True
        assert evaluate(parse("context.n > 5 && state.duration_ms < 50"), env) is False

    def test_logical_op_correctness_no_bug(self):
        # S5.1: ensure logical_op distinguishes and/or
        env_a = {"now": 0, "context": {"a": True, "b": False}, "event": {}, "state": {}, "meta": {}}
        # a && b
        assert evaluate(parse("context.a && context.b"), env_a) is False
        # a || b
        assert evaluate(parse("context.a || context.b"), env_a) is True


# ---------- render_template ----------

class TestRenderTemplate:
    def test_simple_string_var(self):
        env = {"now": 0, "context": {"name": "echo"}, "event": {}, "state": {}, "meta": {}}
        assert render_template("hello {{context.name}}", env) == "hello echo"

    def test_int_to_str(self):
        env = {"now": 0, "context": {"n": 42}, "event": {}, "state": {}, "meta": {}}
        assert render_template("count={{context.n}}", env) == "count=42"

    def test_bool_to_str(self):
        env = {"now": 0, "context": {"b": True}, "event": {}, "state": {}, "meta": {}}
        assert render_template("flag={{context.b}}", env) == "flag=true"

    def test_null_to_empty(self):
        env = {"now": 0, "context": {"x": None}, "event": {}, "state": {}, "meta": {}}
        assert render_template("v=[{{context.x}}]", env) == "v=[]"

    def test_forbidden_namespace_raises(self):
        env = {"now": 0, "secrets": {"k": "v"}}
        with pytest.raises(ExpressionError):
            render_template("{{secrets.k}}", env)

    def test_meta_namespace(self):
        env = {"now": 0, "meta": {"name": "echo"}, "context": {}, "event": {}, "state": {}}
        assert render_template("[{{meta.name}}]", env) == "[echo]"
