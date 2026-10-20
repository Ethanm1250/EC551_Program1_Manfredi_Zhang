"""
Shared evaluator for graph-based circuit formats (BLIF, BENCH, JSON).

A circuit is a set of nodes; each node has a list of fan-in signal names and
a function that maps the fan-ins' truth vectors to the node's truth vector.
Evaluating every node once in topological order gives each primary output's
complete truth table, which is then minimized like any other function.
"""

from ..boolfunc import BooleanFunction, Design, full_mask
from .expr import ParseError, input_env


def gate_function(gtype, full):
    """Return f(list_of_vectors) -> vector for a primitive gate type name."""
    g = gtype.upper()

    def _and(vs):
        acc = full
        for v in vs:
            acc &= v
        return acc

    def _or(vs):
        acc = 0
        for v in vs:
            acc |= v
        return acc

    def _xor(vs):
        acc = 0
        for v in vs:
            acc ^= v
        return acc

    table = {
        "AND": _and,
        "OR": _or,
        "XOR": _xor,
        "NAND": lambda vs: full & ~_and(vs),
        "NOR": lambda vs: full & ~_or(vs),
        "XNOR": lambda vs: full & ~_xor(vs),
        "NOT": lambda vs: full & ~vs[0],
        "INV": lambda vs: full & ~vs[0],
        "BUF": lambda vs: vs[0],
        "BUFF": lambda vs: vs[0],
        "CONST0": lambda vs: 0,
        "CONST1": lambda vs: full,
    }
    if g not in table:
        raise ParseError(f"unsupported gate type '{gtype}'")
    return table[g]


def evaluate_network(name, inputs, nodes, outputs, source=""):
    """
    inputs  : ordered list of primary input names
    nodes   : dict signal -> (func, [fanin names])
    outputs : ordered list of (output_name, driving_signal)
    """
    n = len(inputs)
    values = input_env(inputs)
    full = full_mask(n)

    def compute(target):
        # Iterative post-order DFS (deep circuits would overflow recursion).
        stack = [(target, False)]
        on_path = set()
        while stack:
            sig, expanded = stack.pop()
            if sig in values:
                continue
            if sig not in nodes:
                raise ParseError(f"signal '{sig}' is used but never driven")
            func, fanins = nodes[sig]
            if expanded:
                values[sig] = func([values[f] for f in fanins]) & full
                on_path.discard(sig)
                continue
            if sig in on_path:
                raise ParseError(f"combinational loop through '{sig}'")
            on_path.add(sig)
            stack.append((sig, True))
            for f in fanins:
                if f not in values:
                    if f in on_path:
                        raise ParseError(f"combinational loop through '{f}'")
                    stack.append((f, False))
        return values[target]

    design = Design(name, list(inputs), source=source)
    for out_name, sig in outputs:
        design.add(BooleanFunction(out_name, list(inputs), compute(sig)))
    return design
