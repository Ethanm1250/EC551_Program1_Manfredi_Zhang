"""
Gate-level netlist built from minimized two-level covers.

SOP form :  NOT gates for complemented inputs -> one AND per product term
            -> one OR per output.
POS form :  NOT gates -> one OR per sum clause -> one AND per output.

Inverters and identical product terms / clauses are shared between outputs.
The netlist can be simulated on truth vectors, which lets the engine prove
that the circuit it is about to write as Verilog implements the original
specification before any FPGA tool ever sees it.
"""

import re
from dataclasses import dataclass, field

from .boolfunc import full_mask
from .parsers.expr import input_env
from .parsers.network import gate_function

VERILOG_KEYWORDS = set("""
always and assign automatic begin buf bufif0 bufif1 case casex casez cell cmos config
deassign default defparam design disable edge else end endcase endconfig endfunction
endgenerate endmodule endprimitive endspecify endtable endtask event for force forever
fork function generate genvar highz0 highz1 if ifnone incdir include initial inout input
instance integer join large liblist library localparam macromodule medium module nand
negedge nmos nor noshowcancelled not notif0 notif1 or output parameter pmos posedge
primitive pull0 pull1 pulldown pullup pulsestyle_onevent pulsestyle_ondetect rcmos real
realtime reg release repeat rnmos rpmos rtran rtranif0 rtranif1 scalared showcancelled
signed small specify specparam strong0 strong1 supply0 supply1 table task time tran
tranif0 tranif1 tri tri0 tri1 triand trior trireg unsigned use uwire vectored wait wand
weak0 weak1 while wire wor xnor xor logic bit byte int
""".split())

CONST0, CONST1 = "1'b0", "1'b1"


@dataclass
class Gate:
    kind: str           # and / or / not / buf
    output: str
    inputs: list


@dataclass
class Netlist:
    module: str
    inputs: list                    # Verilog-safe names, same order as design.inputs
    outputs: list
    gates: list = field(default_factory=list)
    wires: list = field(default_factory=list)
    forms: dict = field(default_factory=dict)        # output -> "SOP"/"POS"/"const"
    expressions: dict = field(default_factory=dict)  # output -> human-readable equation

    def simulate(self):
        """Truth vector of every output (gates are stored in topological order)."""
        n = len(self.inputs)
        full = full_mask(n)
        values = input_env(self.inputs)
        values[CONST0], values[CONST1] = 0, full
        for g in self.gates:
            values[g.output] = gate_function(g.kind, full)([values[s] for s in g.inputs]) & full
        return {o: values[o] for o in self.outputs}

    def stats(self):
        counts = {}
        for g in self.gates:
            counts[g.kind] = counts.get(g.kind, 0) + 1
        level = {s: 0 for s in self.inputs + [CONST0, CONST1]}
        for g in self.gates:
            extra = 0 if g.kind == "buf" else 1
            level[g.output] = max(level[s] for s in g.inputs) + extra
        depth = max((level[o] for o in self.outputs), default=0)
        gate_inputs = sum(len(g.inputs) for g in self.gates if g.kind != "buf")
        logic_gates = sum(v for k, v in counts.items() if k != "buf")
        return {"gates": logic_gates, "by_type": counts, "gate_inputs": gate_inputs, "depth": depth}


def verilog_name(name, taken):
    """Make `name` a legal, unique Verilog identifier."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = "n_" + s
    if s in VERILOG_KEYWORDS:
        s += "_"
    base, k = s, 1
    while s in taken:
        s = f"{base}_{k}"
        k += 1
    taken.add(s)
    return s


def build_netlist(design, module, choices):
    """
    choices: dict output_name -> (form, cubes, expression_text) where
        form "SOP": cubes is the minimized cover of F
        form "POS": cubes is the minimized cover of F' (clauses by DeMorgan)
    """
    taken = set()
    ins = [verilog_name(v, taken) for v in design.inputs]
    outs = [verilog_name(o, taken) for o in design.outputs]
    # "top" and "seg7_hex" are the FPGA wrapper's module names.
    nl = Netlist(verilog_name(module, {"top", "seg7_hex"}), ins, outs)
    n = len(ins)
    inverted = {}                 # input index -> wire name of its complement
    shared = {}                   # (kind, literal tuple) -> signal

    def add(kind, output, inputs):
        nl.gates.append(Gate(kind, output, list(inputs)))

    def literal(i, positive):
        if positive:
            return ins[i]
        if i not in inverted:
            w = verilog_name(ins[i] + "_n", taken)
            inverted[i] = w
            nl.wires.append(w)
            add("not", w, [ins[i]])
        return inverted[i]

    def cube_literals(cube, flip):
        """Literal signals of a cube; flip=True inverts them (POS clauses)."""
        value, mask = cube
        lits = []
        for i in range(n):
            bit = 1 << (n - 1 - i)
            if not mask & bit:
                lits.append(literal(i, bool(value & bit) != flip))
        return lits

    # Create every inverter first so they appear at the top of the netlist.
    for out in design.outputs:
        form, cubes, _ = choices[out]
        for c in cubes:
            cube_literals(c, form == "POS")

    for out, vout in zip(design.outputs, outs):
        form, cubes, text = choices[out]
        nl.expressions[vout] = text
        term_kind, final_kind = ("and", "or") if form == "SOP" else ("or", "and")
        flip = form == "POS"
        if not cubes:                                   # SOP: F = 0, POS: F = 1
            add("buf", vout, [CONST0 if form == "SOP" else CONST1])
            nl.forms[vout] = "const"
            continue
        nl.forms[vout] = form
        if len(cubes) == 1:
            lits = cube_literals(cubes[0], flip)
            key = (term_kind, tuple(lits))
            if len(lits) >= 2 and key not in shared:   # single term drives the output directly
                add(term_kind, vout, lits)
                shared[key] = vout
                continue
        signals = []
        for c in cubes:
            lits = cube_literals(c, flip)
            if not lits:                                # empty cube: F = 1 (SOP) / 0 (POS)
                signals.append(CONST1 if form == "SOP" else CONST0)
            elif len(lits) == 1:
                signals.append(lits[0])
            else:
                key = (term_kind, tuple(lits))
                if key not in shared:
                    prefix = "t" if term_kind == "and" else "s"
                    w = verilog_name(f"{prefix}{len(shared)}", taken)
                    nl.wires.append(w)
                    add(term_kind, w, lits)
                    shared[key] = w
                signals.append(shared[key])
        if len(signals) == 1:
            add("buf", vout, signals)
        else:
            add(final_kind, vout, signals)
    return nl
