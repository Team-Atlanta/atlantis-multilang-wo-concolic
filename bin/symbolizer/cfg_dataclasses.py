import copy
from dataclasses import dataclass
from typing import Dict, Set

from graphviz import Digraph

from utils import is_running_under_pytest


@dataclass
class LineInfo:
    function_name: str
    src_file: str
    line_number: int

    def __hash__(self):
        return hash((self.function_name, self.src_file, self.line_number))

    def __eq__(self, other):
        if not isinstance(other, LineInfo):
            return False
        return (self.function_name, self.src_file, self.line_number) == (
            other.function_name,
            other.src_file,
            other.line_number,
        )


@dataclass
class Node:
    addr: int
    addrs: Set[int]
    prevs: Set[int]
    nexts: Set[int]
    instrumented_addrs: Set[int]
    lines: Set[LineInfo]
    reachable_instrumented_addrs: Set[int]
    addrs_reachable_without_any_instrumentation: Set[int]
    lines_from_addrs_reachable_wo_instrumentation: Set[LineInfo]
    fallback: bool

    def __hash__(self):
        return hash(self.addr)

    def __eq__(self, other):
        if isinstance(other, Node):
            return self.addr == other.addr
        return False

    def copy(self) -> "Node":
        return copy.deepcopy(self)

    def print_node(self):
        if is_running_under_pytest():
            print(f"Address: {hex(self.addr)}")
            print(f"Prevs: {[hex(addr) for addr in sorted(self.prevs)]}")
            print(f"Nexts: {[hex(addr) for addr in sorted(self.nexts)]}")
            print(
                f"Instrumented_addrs: {[hex(addr) for addr in sorted(self.instrumented_addrs)]}"
            )
            print(f"Lines: {self.lines}")
            print(
                f"Reachable_instrumented_addrs: {[hex(addr) for addr in sorted(self.reachable_instrumented_addrs)]}"
            )
            print(
                f"Addrs_reachable_without_any_instrumentation: {[hex(addr) for addr in sorted(self.addrs_reachable_without_any_instrumentation)]}"
            )
            print(
                f"lines_from_addrs_reachable_wo_instrumentation: {self.lines_from_addrs_reachable_wo_instrumentation}"
            )


@dataclass
class FunctionCFG:
    name: str
    head: Node
    tail: Set[Node]
    nodes: Dict[int, Node]

    def print_graph(self, benchmark: str, harness: str):
        if is_running_under_pytest():
            dot = Digraph(format="svg")

            for addr, node in self.nodes.items():
                label = f"{hex(addr)}\n{[hex(addr) for addr in sorted(node.instrumented_addrs)] if node.instrumented_addrs else 'NOT INSTRUMENTED!'}\n{sorted([line.line_number for line in node.lines])}\n{[hex(addr) for addr in sorted(node.reachable_instrumented_addrs)]}\n{[hex(addr) for addr in sorted(node.addrs_reachable_without_any_instrumentation)]}\n"
                dot.node(f"{hex(addr)}", label)

                for next_addr in node.nexts:
                    dot.edge(f"{hex(addr)}", f"{hex(next_addr)}")

            dot.render(f"cfg_{benchmark}_{harness}_{self.name}")
