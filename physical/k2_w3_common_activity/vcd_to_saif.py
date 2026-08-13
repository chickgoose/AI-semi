#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import pathlib
import re


@dataclass
class BitActivity:
    state: str = "x"
    last: int = 0
    t0: int = 0
    t1: int = 0
    tx: int = 0
    tc: int = 0

    def change(self, value: str, now: int) -> None:
        elapsed = now-self.last
        if elapsed < 0:
            raise ValueError("non-monotonic VCD")
        if self.state == "0": self.t0 += elapsed
        elif self.state == "1": self.t1 += elapsed
        else: self.tx += elapsed
        if value != self.state: self.tc += 1
        self.state, self.last = value, now


@dataclass
class Variable:
    scope: tuple[str, ...]
    reference: str
    width: int
    bits: list[BitActivity] = field(default_factory=list)


def parse_vcd(text: str) -> tuple[int, list[Variable]]:
    lines = text.splitlines(); scopes: list[str] = []; by_code = {}
    variables: list[Variable] = []; position = 0
    for position, line in enumerate(lines):
        words = line.split()
        if words[:1] == ["$scope"]: scopes.append(words[2])
        elif words[:1] == ["$upscope"]: scopes.pop()
        elif words[:1] == ["$var"]:
            width, code = int(words[2]), words[3]
            reference = " ".join(words[4:-1])
            reference = re.sub(r"\s*\[[^]]+\]\s*$", "", reference)
            variable = Variable(tuple(scopes), reference, width,
                                [BitActivity() for _ in range(width)])
            by_code.setdefault(code, []).append(variable); variables.append(variable)
        elif "$enddefinitions" in line: break
    else: raise ValueError("VCD enddefinitions missing")
    now = 0
    for line in lines[position+1:]:
        line = line.strip()
        if not line or line.startswith("$"): continue
        if line.startswith("#"):
            now = int(line[1:]); continue
        if line[0] in "01xXzZ": value, code = line[0].lower(), line[1:]
        elif line[0] in "bB":
            pieces=line[1:].split()
            if len(pieces)!=2: raise ValueError("malformed vector change")
            value,code=pieces[0].lower(),pieces[1]
        else: continue
        aliases=by_code.get(code)
        if aliases is None: raise ValueError("change for unknown VCD identifier")
        for variable in aliases:
            expanded=value.replace("z","x").rjust(variable.width,value[0])[-variable.width:]
            for activity,bit in zip(variable.bits,expanded): activity.change(bit,now)
    if now <= 0: raise ValueError("VCD has no positive duration")
    for variable in variables:
        for activity in variable.bits: activity.change(activity.state,now)
    return now,variables


def saif_name(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*(?:\[[0-9]+\])?",name):
        return name
    return "\\"+name.replace(" ","_")+" "


def build_tree(variables: list[Variable]) -> dict:
    tree={"nets":[],"children":{}}
    for variable in variables:
        if "dut" not in variable.scope: continue
        index=variable.scope.index("dut"); node=tree
        for scope in variable.scope[index+1:]:
            node=node["children"].setdefault(scope,{"nets":[],"children":{}})
        for bit_index,activity in enumerate(variable.bits):
            name=variable.reference
            if variable.width>1: name=f"{name}[{variable.width-1-bit_index}]"
            node["nets"].append((name,activity))
    if not tree["nets"] and not tree["children"]:
        raise ValueError("VCD contains no dut scope")
    return tree


def emit_instance(name: str,tree: dict,indent: str="  ") -> list[str]:
    lines=[f"{indent}(INSTANCE {saif_name(name)}"]
    if tree["nets"]:
        lines.append(f"{indent}  (NET")
        for net,activity in sorted(tree["nets"],key=lambda item:item[0]):
            lines.append(f"{indent}    ({saif_name(net)} (T0 {activity.t0}) "
                         f"(T1 {activity.t1}) (TX {activity.tx}) "
                         f"(TC {activity.tc}) (IG 0))")
        lines.append(f"{indent}  )")
    for child,subtree in sorted(tree["children"].items()):
        lines.extend(emit_instance(child,subtree,indent+"  "))
    lines.append(f"{indent})")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vcd", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite SAIF")
    try: duration,variables=parse_vcd(args.vcd.read_text()); tree=build_tree(variables)
    except ValueError as exc: raise SystemExit(str(exc)) from exc
    lines=['(SAIFILE','  (SAIFVERSION "2.0")','  (DIRECTION "backward")',
           '  (DESIGN "staged_common_activity")','  (DATE "")',
           '  (VENDOR "A6 frozen common TB producer")',
           '  (PROGRAM_NAME "vcd_to_saif.py")','  (VERSION "1")',
           '  (DIVIDER /)','  (TIMESCALE 1 ps)',f'  (DURATION {duration})']
    lines.extend(emit_instance("dut",tree)); lines.append(')')
    args.output.write_text("\n".join(lines)+"\n")


if __name__ == "__main__":
    main()
