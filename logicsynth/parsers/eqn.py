"""
Equation-file parser (.eqn / .txt / command-line expressions).

One equation per line (or separated by ';').  Comments start with # or //.

    F(A,B,C,D) = A'B'C + AB + CD'          # SOP (declared inputs, compact names)
    G(A,B,C)   = (A + B')(B + C)           # POS / any multi-level expression
    H(A,B,C,D) = m(1,3,7,11,15) + d(0,2,5) # minterm list with don't-cares
    K(A,B,C)   = M(0,4,6)                  # maxterm list (product of maxterms)
    out        = a & ~b | c ^ d            # inputs inferred from usage

ABC-style EQN files are accepted too:

    INORDER = a b c;
    OUTORDER = f;
    n1 = a * !b;
    f  = n1 + c;

A left-hand-side name that is used on the right-hand side of another
equation is an intermediate node (multi-level logic), not an output, unless
OUTORDER lists it.
"""

import re

from ..boolfunc import BooleanFunction, Design, full_mask, indices_to_vector
from .expr import ParseError, evaluate, input_env, parse_expression

_LHS_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?\s*$")
_NUMS = r"([\d\s,]*)"
_MIN_RE = re.compile(r"^\s*(?:(?:sum|Sum|SUM)\s*m?|m)\s*\(" + _NUMS + r"\)\s*$")
_MAX_RE = re.compile(r"^\s*(?:(?:prod|Prod|PROD)\s*M?|M)\s*\(" + _NUMS + r"\)\s*$")
_DC_RE = re.compile(r"^(.*?)\s*[+*,]?\s*\b(?:dc|d)\s*\(" + _NUMS + r"\)\s*$", re.S)
_ORDER_RE = re.compile(r"^\s*(INORDER|OUTORDER)\s*=\s*(.*)$", re.I)


def _numbers(text):
    return [int(t) for t in re.split(r"[\s,]+", text.strip()) if t]


def _strip_comment(line):
    line = line.split("#", 1)[0]
    return line.split("//", 1)[0]


def _statements(text):
    for raw in text.splitlines():
        for stmt in _strip_comment(raw).split(";"):
            if stmt.strip():
                yield stmt.strip()


def parse_equations(text, name="design", default_inputs=None):
    inorder = outorder = None
    declared = []                       # union of all F(A,B,..) declarations
    equations = []                      # (lhs, rhs) in file order

    for stmt in _statements(text):
        m = _ORDER_RE.match(stmt)
        if m:
            names = m.group(2).split()
            if m.group(1).upper() == "INORDER":
                inorder = names
            else:
                outorder = names
            continue
        if "=" in stmt:
            lhs, rhs = stmt.split("=", 1)
        else:                           # bare expression -> output "F"
            lhs, rhs = "F", stmt
        lm = _LHS_RE.match(lhs)
        if not lm:
            raise ParseError(f"bad left-hand side: {lhs.strip()!r}")
        out_name, decl = lm.groups()
        if decl is not None:
            for v in (x.strip() for x in decl.split(",")):
                if v and v not in declared:
                    declared.append(v)
        equations.append((out_name, rhs.strip()))

    if not equations:
        raise ParseError("no equations found")

    lhs_names = [e[0] for e in equations]
    if len(set(lhs_names)) != len(lhs_names):
        raise ParseError("a signal is defined more than once")
    signals = set(lhs_names)
    inputs = inorder or declared or (list(default_inputs) if default_inputs else None)
    split_names = inputs                # enables "AB'C" style splitting when known

    # First pass: classify each right-hand side and parse expressions.
    parsed = {}                         # lhs -> ("terms", on_list, is_max, dc) | ("expr", ast, dc)
    used_order = []
    for lhs, rhs in equations:
        dc = []
        dm = _DC_RE.match(rhs)
        if dm and dm.group(1).strip():
            rhs, dc = dm.group(1), _numbers(dm.group(2))
        mm, xm = _MIN_RE.match(rhs), _MAX_RE.match(rhs)
        if mm or xm:
            parsed[lhs] = ("terms", _numbers((mm or xm).group(1)), bool(xm), dc)
            continue
        ast, used = parse_expression(rhs, split_names, signals)
        parsed[lhs] = ("expr", ast, dc)
        for v in used:
            if v not in used_order:
                used_order.append(v)

    if inputs is None:
        inputs = [v for v in used_order if v not in signals]
    unknown = [v for v in used_order if v not in signals and v not in inputs]
    if unknown:
        raise ParseError(f"variables {unknown} are not in the declared input list {inputs}")
    if any(p[0] == "terms" for p in parsed.values()) and not inputs:
        raise ParseError("minterm/maxterm lists need declared inputs, e.g. F(A,B,C) = m(1,2)")

    n = len(inputs)
    full = full_mask(n)
    env = input_env(inputs)
    values = {}

    def resolve(sig, stack=()):
        if sig in values:
            return values[sig]
        if sig in stack:
            raise ParseError(f"combinational loop through '{sig}'")
        entry = parsed[sig]
        if entry[0] == "terms":
            _, nums, is_max, dc = entry
            bad = [x for x in nums + dc if x >= (1 << n)]
            if bad:
                raise ParseError(f"term index {bad[0]} out of range for {n} inputs")
            vec = indices_to_vector(nums)
            vec = full & ~vec if is_max else vec
        else:
            local = dict(env)
            for dep in _signals_in(entry[1]) & signals:
                local[dep] = resolve(dep, stack + (sig,))
            vec = evaluate(entry[1], local, n)
        values[sig] = vec
        return vec

    referenced = set()
    for entry in parsed.values():
        if entry[0] == "expr":
            referenced |= _signals_in(entry[1]) & signals
    outputs = outorder or [s for s in lhs_names if s not in referenced] or lhs_names

    design = Design(name, list(inputs))
    for out in outputs:
        if out not in parsed:
            raise ParseError(f"output '{out}' is never defined")
        dc_list = parsed[out][-1]
        design.add(BooleanFunction(out, list(inputs), resolve(out), indices_to_vector(dc_list)))
    return design


def _signals_in(ast):
    """Set of variable names referenced by an AST."""
    if ast[0] == "var":
        return {ast[1]}
    if ast[0] == "const":
        return set()
    if ast[0] == "not":
        return _signals_in(ast[1])
    out = set()
    for x in ast[1]:
        out |= _signals_in(x)
    return out
