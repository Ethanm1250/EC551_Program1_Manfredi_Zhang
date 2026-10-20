"""
Input front-ends.  Every parser returns a boolfunc.Design (an ordered input
list plus one truth-table-backed BooleanFunction per output), so the rest of
the engine never needs to know which format the design came from.

    Algebraic : .eqn .txt .expr   (SOP / POS / multi-level / minterm lists)
                .pla              (Espresso two-level cover)
    Circuit   : .blif             (Berkeley logic network)
                .bench            (ISCAS gate netlist)
                .json             (our JSON gate graph)
"""

import os

from .blif import parse_blif
from .bench import parse_bench
from .eqn import parse_equations
from .expr import ParseError
from .jsoncircuit import parse_json_circuit
from .pla import parse_pla

PARSERS = {
    ".eqn": parse_equations,
    ".txt": parse_equations,
    ".expr": parse_equations,
    ".pla": parse_pla,
    ".blif": parse_blif,
    ".bench": parse_bench,
    ".json": parse_json_circuit,
}


def load_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in PARSERS:
        raise ParseError(f"unknown file type '{ext}' (supported: {', '.join(sorted(PARSERS))})")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    name = os.path.splitext(os.path.basename(path))[0]
    design = PARSERS[ext](text, name=name)
    design.source = path
    return design


def load_expression(text, inputs=None):
    """Parse equation text typed on the command line / REPL."""
    design = parse_equations(text, name="expr", default_inputs=inputs)
    design.source = "expression"
    return design


__all__ = ["load_file", "load_expression", "ParseError", "PARSERS"]
