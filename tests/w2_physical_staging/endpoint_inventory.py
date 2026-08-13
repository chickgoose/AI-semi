#!/usr/bin/env python3
"""Validate explicit endpoint leaves without confusing them with top totals."""

from collections import Counter
import copy
import json


ROLE_TYPES = {
    "clock_gate": "TLATNTSCAX2",
    "symbol_mux_bit": "MX2X1",
    "rise_capture_bit": "DFFRHQX1",
    "fall_capture_bit": "DFFNSRX1",
}
ROLE_PREFIXES = {
    "clock_gate": "w2_ep_icg_",
    "symbol_mux_bit": "w2_ep_mux_",
    "rise_capture_bit": "w2_ep_pos_",
    "fall_capture_bit": "w2_ep_neg_",
}


class InventoryError(ValueError):
    pass


def _attribute(cell, name):
    return str(cell.get("attributes", {}).get(name, "")).strip('"')


def _leaf_basename(name, cell):
    hdlname = _attribute(cell, "hdlname")
    if hdlname:
        return hdlname.replace("\\", "").split()[-1].split(".")[-1]
    return name.replace("\\", "").split(".")[-1]


def _expect(condition, message):
    if not condition:
        raise InventoryError(message)


def validate_yosys_json(data, top_name, root_tag, expected_counts):
    """Return endpoint and whole-top counts from a Yosys mapped JSON object.

    The endpoint module is selected only through the preserved root attribute.
    Whole-top counts are diagnostic and may exceed endpoint counts.
    """
    modules = data.get("modules", {})
    _expect(top_name in modules, f"missing top module {top_name}")
    top = modules[top_name]
    roots = [(name, cell) for name, cell in top.get("cells", {}).items()
             if _attribute(cell, "w2_endpoint_root") == root_tag]
    _expect(len(roots) == 1, f"expected one {root_tag} endpoint root, got {len(roots)}")
    root_name, root = roots[0]
    _expect("w2_endpoint_link__" in root_name or
            "w2_endpoint_link__" in _attribute(root, "hdlname"),
            "endpoint root lost stable w2_endpoint_link__ prefix")
    endpoint_type = root["type"]
    _expect(endpoint_type in modules, f"endpoint module {endpoint_type} missing")
    endpoint = modules[endpoint_type]

    role_counts = Counter()
    type_counts = Counter()
    leaves = []
    for name, cell in endpoint.get("cells", {}).items():
        role = _attribute(cell, "w2_endpoint_leaf_role")
        if not role:
            continue
        _expect(role in ROLE_TYPES, f"unknown endpoint leaf role {role}")
        _expect(cell["type"] == ROLE_TYPES[role],
                f"wrong cell type for {role}: {cell['type']}")
        base = _leaf_basename(name, cell)
        _expect(base.startswith(ROLE_PREFIXES[role]),
                f"leaf {name} lost stable {ROLE_PREFIXES[role]} prefix")
        _expect(_attribute(cell, "keep") == "true", f"leaf {name} missing keep")
        _expect(_attribute(cell, "dont_touch") == "true",
                f"leaf {name} missing dont_touch")
        role_counts[role] += 1
        type_counts[cell["type"]] += 1
        leaves.append((name, role, cell))

    _expect(dict(type_counts) == expected_counts,
            f"endpoint counts {dict(type_counts)} != {expected_counts}")

    ports = endpoint.get("ports", {})
    clk_name = "p6_clk_o" if root_tag == "p6" else "burst_clk_o"
    data_name = "p6_data_o" if root_tag == "p6" else "burst_data_o"
    ref = ports["ref_clk_i"]["bits"]
    sample = ports["sample_clk_i"]["bits"]
    rst = ports["rst_n"]["bits"]
    link_clk = ports[clk_name]["bits"]
    link_data = ports[data_name]["bits"]

    seen_y = []
    seen_pos_d = []
    for name, role, cell in leaves:
        con = cell.get("connections", {})
        if role == "clock_gate":
            _expect(con.get("CK") == sample, f"{name} CK is not sample clock")
            _expect(con.get("SE") == ["0"], f"{name} SE is not zero")
            _expect(con.get("ECK") == link_clk, f"{name} ECK is not link clock")
        elif role == "symbol_mux_bit":
            _expect(con.get("S0") == ref, f"{name} S0 is not ref clock")
            seen_y += con.get("Y", [])
        elif role == "rise_capture_bit":
            _expect(con.get("CK") == link_clk, f"{name} CK is not link clock")
            _expect(con.get("RN") == rst, f"{name} RN is not rst_n")
            seen_pos_d += con.get("D", [])
        elif role == "fall_capture_bit":
            _expect(con.get("CKN") == link_clk, f"{name} CKN is not link clock")
            _expect(con.get("RN") == rst, f"{name} RN is not rst_n")
            _expect(con.get("SN") == ["1"], f"{name} SN is not one")
            _expect(con.get("QN") == [], f"{name} QN is not open")
    _expect(sorted(seen_y) == sorted(link_data), "mux outputs do not cover link data")
    _expect(sorted(seen_pos_d) == sorted(link_data),
            "posedge capture inputs do not cover link data")

    whole = recursive_external_counts(data, top_name)
    return {"root": root_name, "endpoint_type": endpoint_type,
            "endpoint_counts": dict(type_counts), "whole_top_counts": whole}


def recursive_external_counts(data, top_name):
    modules = data["modules"]
    out = Counter()

    def walk(module_name, multiplier=1, stack=()):
        _expect(module_name not in stack, f"recursive module {module_name}")
        module = modules[module_name]
        for cell in module.get("cells", {}).values():
            cell_type = cell["type"]
            if cell_type in ROLE_TYPES.values():
                out[cell_type] += multiplier
            elif cell_type in modules and not cell_type.startswith("$"):
                walk(cell_type, multiplier, stack + (module_name,))
    walk(top_name)
    return dict(out)


def validate_receipt_fixture(receipt):
    expected = receipt["endpoint_required_counts"]
    actual = Counter(leaf["cell"] for leaf in receipt["endpoint_leaves"])
    _expect(dict(actual) == expected, "fixture endpoint counts mismatch")
    for leaf in receipt["endpoint_leaves"]:
        role = leaf["role"]
        _expect(leaf["cell"] == ROLE_TYPES[role], "fixture role/type mismatch")
        _expect(leaf["name"].startswith(ROLE_PREFIXES[role]),
                "fixture stable prefix mismatch")
        _expect(leaf["connectivity"] == receipt["required_connectivity"][role],
                f"fixture wrong connectivity for {role}")
    for cell, count in expected.items():
        _expect(receipt["whole_top_counts"].get(cell, 0) >= count,
                f"whole top omits endpoint {cell}")
    return copy.deepcopy(receipt)


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
