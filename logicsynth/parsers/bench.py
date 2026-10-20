"""
ISCAS-85 .bench netlist parser (gate-level circuit graph).

    INPUT(a)
    INPUT(b)
    OUTPUT(f)
    n1 = NAND(a, b)
    f  = NOT(n1)

Supported gates: AND OR NAND NOR XOR XNOR NOT BUF/BUFF.  Purely numeric
signal names (as in the ISCAS benchmarks) are prefixed with 'N' so they stay
readable in expressions and legal in Verilog.
"""

import re

from ..boolfunc import full_mask
from .expr import ParseError
from .network import evaluate_network, gate_function

_IO_RE = re.compile(r"^(INPUT|OUTPUT)\s*\(\s*([^)\s]+)\s*\)$", re.I)
_GATE_RE = re.compile(r"^([^=\s]+)\s*=\s*([A-Za-z]+)\s*\(([^)]*)\)$")


def _name(s):
    return "N" + s if s.isdigit() else s


def parse_bench(text, name="design"):
    inputs, outputs, gates = [], [], []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _IO_RE.match(line)
        if m:
            (inputs if m.group(1).upper() == "INPUT" else outputs).append(_name(m.group(2)))
            continue
        m = _GATE_RE.match(line)
        if not m:
            raise ParseError(f"bad .bench line: {line!r}")
        out, gtype, args = m.groups()
        if gtype.upper() == "DFF":
            raise ParseError("DFF found: only combinational .bench circuits are supported")
        fanins = [_name(a.strip()) for a in args.split(",") if a.strip()]
        gates.append((_name(out), gtype, fanins))

    if not inputs or not outputs:
        raise ParseError(".bench file needs INPUT(...) and OUTPUT(...) lines")
    full = full_mask(len(inputs))
    nodes = {out: (gate_function(gtype, full), fanins) for out, gtype, fanins in gates}
    return evaluate_network(name, inputs, nodes, [(o, o) for o in outputs], source="bench")
