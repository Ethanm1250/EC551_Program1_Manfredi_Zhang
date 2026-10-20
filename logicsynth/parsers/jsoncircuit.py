"""
JSON circuit-graph format (our own design).

Nodes are gates, edges are given by each gate's list of input signals:

    {
      "name": "mux2",
      "inputs":  ["s", "a", "b"],
      "outputs": ["y"],
      "gates": [
        {"name": "s_n", "type": "NOT", "inputs": ["s"]},
        {"name": "t0",  "type": "AND", "inputs": ["s_n", "a"]},
        {"name": "t1",  "type": "AND", "inputs": ["s", "b"]},
        {"name": "y",   "type": "OR",  "inputs": ["t0", "t1"]}
      ]
    }

"outputs" may also be an object mapping output names to driving signals,
e.g. {"y": "t2"}.  Gate types: AND OR NAND NOR XOR XNOR NOT BUF CONST0 CONST1.
"""

import json

from ..boolfunc import full_mask
from .expr import ParseError
from .network import evaluate_network, gate_function


def parse_json_circuit(text, name="design"):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ParseError(f"invalid JSON: {e}") from None

    inputs = data.get("inputs")
    outs = data.get("outputs")
    gates = data.get("gates", data.get("nodes"))
    if not inputs or not outs or gates is None:
        raise ParseError('JSON circuit needs "inputs", "outputs" and "gates"')

    full = full_mask(len(inputs))
    nodes = {}
    for g in gates:
        try:
            gname, gtype = g["name"], g["type"]
        except KeyError:
            raise ParseError(f'gate needs "name" and "type": {g}') from None
        if gname in nodes or gname in inputs:
            raise ParseError(f"signal '{gname}' is driven more than once")
        nodes[gname] = (gate_function(gtype, full), list(g.get("inputs", [])))

    if isinstance(outs, dict):
        outputs = list(outs.items())
    else:
        outputs = [(o, o) for o in outs]
    return evaluate_network(data.get("name", name), inputs, nodes, outputs, source="json")
