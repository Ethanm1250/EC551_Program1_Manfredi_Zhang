"""
BLIF (Berkeley Logic Interchange Format) parser - combinational subset.

    .model full_adder
    .inputs a b cin
    .outputs sum cout
    .names a b cin sum      # node 'sum' = cover over its fan-ins
    100 1
    010 1
    001 1
    111 1
    .names a b cin cout
    11- 1
    1-1 1
    -11 1
    .end

Each .names block is a node of the circuit graph.  Rows ending in 1 list the
ON-set cover of the node; rows ending in 0 list its OFF-set (node = NOT of
that cover).  A .names with no fan-ins is a constant ("1" row -> 1).
.latch / .subckt (sequential / hierarchical) are not supported.
"""

from .expr import ParseError
from .network import evaluate_network


def _logical_lines(text):
    """Strip comments and join backslash-continued lines."""
    buf = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        line = (buf + line).strip()
        buf = ""
        if line:
            yield line


def _cover_function(rows):
    """Build f(vectors) for a .names cover.  rows: list of (pattern, out_bit).

    Intermediate results use Python's infinite-width ints (-1 = all ones);
    evaluate_network masks every node's result to 2**n bits.
    """
    polarity = {o for _, o in rows}
    if len(polarity) > 1:
        raise ParseError("a .names cover mixes ON-set and OFF-set rows")
    off_cover = polarity == {"0"}

    def func(vs, rows=rows):
        acc = 0
        for pattern, _ in rows:
            term = -1
            for ch, v in zip(pattern, vs):
                if ch == "1":
                    term &= v
                elif ch == "0":
                    term &= ~v
            acc |= term
        return ~acc if off_cover else acc

    return func


def parse_blif(text, name="design"):
    inputs, outputs = [], []
    nodes = {}
    model = name
    current = None                          # (signal, fanins, rows) being read

    def close():
        nonlocal current
        if current:
            sig, fanins, rows = current
            if not fanins:                  # constant node
                value = any(o == "1" for _, o in rows)
                nodes[sig] = ((lambda vs, value=value: -1 if value else 0), [])
            else:
                nodes[sig] = (_cover_function(rows), fanins)
            current = None

    for line in _logical_lines(text):
        if line.startswith("."):
            close()
            parts = line.split()
            kw = parts[0]
            if kw == ".model":
                model = parts[1] if len(parts) > 1 else name
            elif kw == ".inputs":
                inputs.extend(parts[1:])
            elif kw == ".outputs":
                outputs.extend(parts[1:])
            elif kw == ".names":
                if len(parts) < 2:
                    raise ParseError(".names needs at least an output signal")
                current = (parts[-1], parts[1:-1], [])
            elif kw == ".end":
                break
            elif kw in (".latch", ".subckt", ".gate", ".mlatch"):
                raise ParseError(f"{kw} is not supported (combinational BLIF only)")
            # other dot-commands (.default_input_arrival, ...) are ignored
            continue
        if current is None:
            raise ParseError(f"unexpected line outside a .names block: {line!r}")
        parts = line.split()
        sig, fanins, rows = current
        if not fanins:
            rows.append(("", parts[0]))
        else:
            if len(parts) != 2 or len(parts[0]) != len(fanins):
                raise ParseError(f"bad cover row {line!r} for node '{sig}'")
            rows.append((parts[0], parts[1]))
    close()

    if not inputs or not outputs:
        raise ParseError("BLIF file needs .inputs and .outputs")
    return evaluate_network(model, inputs, nodes, [(o, o) for o in outputs], source="blif")
