"""
Boolean expression parser (SOP, POS and general multi-level expressions).

Accepted syntax (operators listed from lowest to highest precedence):

    OR    :  +   |
    XOR   :  ^
    AND   :  *   &   .   or plain juxtaposition ("A B", "A(B+C)", "AB" if A,B declared)
    NOT   :  prefix ~ or !,  postfix '      (A' == ~A == !A)
    atoms :  identifiers, 0, 1, ( ... )

Identifiers are [A-Za-z_][A-Za-z0-9_]*.  When the variable list is declared
(e.g. "F(A,B,C) = AB'C + A'B"), an identifier that is not itself a declared
name is split into declared names by longest match, so "AB'C" reads as
A B' C.  Without a declaration every identifier is one variable.

The parser builds a small AST of tuples:
    ("var", name) ("const", 0|1) ("not", x) ("and", [..]) ("or", [..]) ("xor", [..])
which `evaluate` turns into a truth vector (see boolfunc.py).
"""

import re

from ..boolfunc import full_mask, var_vector


class ParseError(ValueError):
    pass


_TOKEN_RE = re.compile(r"\s*(?:([A-Za-z_][A-Za-z0-9_]*)|([01])|(.))")


def tokenize(text):
    tokens = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            break
        ident, const, sym = m.groups()
        if ident is not None:
            tokens.append(("id", ident))
        elif const is not None:
            tokens.append(("const", int(const)))
        elif sym is not None:
            if sym not in "+|^*&.~!'()":
                raise ParseError(f"unexpected character {sym!r} in expression")
            tokens.append(("op", sym))
        pos = m.end()
    return tokens


def _split_identifier(ident, names):
    """Split an identifier into declared variable names (longest match first)."""
    if ident in names:
        return [ident]
    for k in range(len(ident) - 1, 0, -1):
        head = ident[:k]
        if head in names:
            rest = _split_identifier(ident[k:], names)
            if rest is not None:
                return [head] + rest
    return None


class _Parser:
    def __init__(self, tokens, declared, signals):
        self.declared = declared        # declared input names (None = free)
        self.signals = signals or set() # extra known names (EQN intermediate nodes)
        self.toks = self._expand(tokens)
        self.i = 0
        self.used = []                  # variables in order of first use

    def _expand(self, tokens):
        """Apply the declared-name splitting rule to identifier tokens."""
        if self.declared is None:
            return tokens
        known = set(self.declared) | self.signals
        out = []
        for kind, val in tokens:
            if kind == "id" and val not in known:
                parts = _split_identifier(val, known)
                if parts is None:
                    raise ParseError(f"unknown variable '{val}' (declared: {', '.join(self.declared)})")
                out.extend(("id", p) for p in parts)
            else:
                out.append((kind, val))
        return out

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def accept(self, *ops):
        kind, val = self.peek()
        if kind == "op" and val in ops:
            self.i += 1
            return True
        return False

    def parse(self):
        if not self.toks:
            raise ParseError("empty expression")
        node = self.parse_or()
        if self.i != len(self.toks):
            raise ParseError(f"unexpected token {self.peek()[1]!r}")
        return node

    def parse_or(self):
        items = [self.parse_xor()]
        while self.accept("+", "|"):
            items.append(self.parse_xor())
        return items[0] if len(items) == 1 else ("or", items)

    def parse_xor(self):
        items = [self.parse_and()]
        while self.accept("^"):
            items.append(self.parse_and())
        return items[0] if len(items) == 1 else ("xor", items)

    def _starts_unary(self):
        kind, val = self.peek()
        return kind in ("id", "const") or (kind == "op" and val in "(~!")

    def parse_and(self):
        items = [self.parse_unary()]
        while True:
            if self.accept("*", "&", "."):
                items.append(self.parse_unary())
            elif self._starts_unary():          # juxtaposition = AND
                items.append(self.parse_unary())
            else:
                break
        return items[0] if len(items) == 1 else ("and", items)

    def parse_unary(self):
        if self.accept("~", "!"):
            return ("not", self.parse_unary())
        node = self.parse_atom()
        while self.accept("'"):
            node = ("not", node)
        return node

    def parse_atom(self):
        kind, val = self.take()
        if kind == "id":
            if val not in self.used:
                self.used.append(val)
            return ("var", val)
        if kind == "const":
            return ("const", val)
        if kind == "op" and val == "(":
            node = self.parse_or()
            if not self.accept(")"):
                raise ParseError("missing ')'")
            return node
        raise ParseError(f"unexpected token {val!r}" if val is not None else "unexpected end of expression")


def parse_expression(text, declared=None, signals=None):
    """Parse an expression string.  Returns (ast, variables_in_order_of_use)."""
    p = _Parser(tokenize(text), declared, signals)
    ast = p.parse()
    return ast, p.used


def evaluate(ast, env, n):
    """Evaluate an AST to a truth vector.  env maps name -> truth vector."""
    full = full_mask(n)
    kind = ast[0]
    if kind == "var":
        return env[ast[1]]
    if kind == "const":
        return full if ast[1] else 0
    if kind == "not":
        return full & ~evaluate(ast[1], env, n)
    vals = [evaluate(x, env, n) for x in ast[1]]
    acc = vals[0]
    for v in vals[1:]:
        if kind == "and":
            acc &= v
        elif kind == "or":
            acc |= v
        else:
            acc ^= v
    return acc


def input_env(inputs):
    """Environment mapping each input name to its truth vector."""
    n = len(inputs)
    return {name: var_vector(i, n) for i, name in enumerate(inputs)}
