"""
PLA (Espresso) format parser.

    .i 4                # number of inputs
    .o 1                # number of outputs
    .ilb A B C D        # input names (optional)
    .ob F               # output names (optional)
    .type fd            # f, fd (default), fr, fdr
    .p 3
    00-1 1
    1-11 1
    0000 -
    .e

Input columns use 0 / 1 / - ; output columns use
    1     -> cube is in the ON-set of that output
    0     -> cube is in the OFF-set (types fr / fdr only)
    - , 2 -> cube is in the don't-care set (types fd / fdr)
    ~     -> no meaning for this output
"""

from ..boolfunc import BooleanFunction, Design, full_mask
from .expr import ParseError, input_env


def parse_pla(text, name="design"):
    n_in = n_out = None
    ilb = ob = None
    ptype = "fd"
    rows = []

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("."):
            parts = line.split()
            kw = parts[0]
            if kw == ".i":
                n_in = int(parts[1])
            elif kw == ".o":
                n_out = int(parts[1])
            elif kw == ".ilb":
                ilb = parts[1:]
            elif kw == ".ob":
                ob = parts[1:]
            elif kw == ".type":
                ptype = parts[1].lower()
            elif kw in (".e", ".end"):
                break
            continue                    # .p, .phase, etc. are informational
        parts = line.split()
        if len(parts) == 1 and n_in is not None:   # "1-0|1" without a space
            parts = [line[:n_in], line[n_in:]]
        if len(parts) != 2:
            raise ParseError(f"bad PLA row: {line!r}")
        rows.append((parts[0].replace("|", ""), parts[1].replace("|", "")))

    if n_in is None:
        if not rows:
            raise ParseError("empty PLA file")
        n_in = len(rows[0][0])
    if n_out is None:
        n_out = len(rows[0][1]) if rows else 1
    inputs = ilb or [f"x{i}" for i in range(n_in)]
    outputs = ob or ([f"f{j}" for j in range(n_out)] if n_out > 1 else ["F"])
    if len(inputs) != n_in or len(outputs) != n_out:
        raise ParseError(".ilb/.ob name count does not match .i/.o")
    if ptype not in ("f", "fd", "fr", "fdr"):
        raise ParseError(f"unsupported .type {ptype}")

    full = full_mask(n_in)
    env = input_env(inputs)
    on = [0] * n_out
    off = [0] * n_out
    dc = [0] * n_out
    for cube, outs in rows:
        if len(cube) != n_in or len(outs) != n_out:
            raise ParseError(f"row {cube} {outs} does not match .i {n_in} / .o {n_out}")
        vec = full
        for ch, name_ in zip(cube, inputs):
            if ch == "1":
                vec &= env[name_]
            elif ch == "0":
                vec &= ~env[name_]
            elif ch not in "-2":
                raise ParseError(f"bad input character {ch!r} in {cube}")
        vec &= full
        for j, ch in enumerate(outs):
            if ch in "14":
                on[j] |= vec
            elif ch == "0":
                off[j] |= vec
            elif ch in "-2":
                dc[j] |= vec

    design = Design(name, list(inputs), source="pla")
    for j, out in enumerate(outputs):
        if ptype == "f":
            dcv = 0
        elif ptype == "fd":
            dcv = dc[j]
        elif ptype == "fr":
            dcv = full & ~(on[j] | off[j])          # unspecified points are don't-care
        else:                                       # fdr
            dcv = dc[j] | (full & ~(on[j] | off[j]))
        design.add(BooleanFunction(out, list(inputs), on[j], dcv))
    return design
