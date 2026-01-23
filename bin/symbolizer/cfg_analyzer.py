#!/usr/bin/env python3

import argparse
import multiprocessing
import os
import pickle
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Set
from urllib.parse import urlparse

from redis import Redis

from cfg_dataclasses import FunctionCFG, LineInfo, Node
from llvm_symbolizer import LLVMSymbolizer
from utils import is_running_under_pytest


@dataclass
class IntermediateData:
    addr: int
    next_addr: Set[int]
    instruction: str
    node_start: bool


class CFGWorker:
    def __init__(
        self, harness: str, llvm_symbolizer_path: str, worker_id: int, num_workers: int
    ) -> None:
        self.harness = harness
        self.llvm_symbolizer_path = llvm_symbolizer_path
        self.worker_id = worker_id
        self.num_workers = num_workers

    def __run_objdump(self) -> List[FunctionCFG]:
        cmd = ["objdump", "-d", self.harness]
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        output, _ = process.communicate()
        regions = output.split("\n\n")
        regions = regions[self.worker_id :: self.num_workers]

        data: List[FunctionCFG] = []

        for region in regions:
            if "__TMC_END__" not in region:
                continue
            lines = region.split("\n")
            function_name = re.search(r"<(.*?)>", lines[0]).group(1)

            if function_name in [
                "deregister_tm_clones",
                "register_tm_clones",
                "sancov.module_ctor_8bit_counters",
            ]:
                continue

            node_start = True
            intermediate_data: List[IntermediateData] = []
            addr_to_index = {}
            jumps: List[int] = []
            index = 0
            intermediate_datum = None
            instruction = None

            for line in lines:
                if line.count("\t") != 2:
                    continue
                if "xchg   %ax,%ax" in line.split("\t")[2].strip():
                    continue
                if "nop" in line.split("\t")[2].strip().split("<")[0]:
                    continue
                addr = int(line.split("\t")[0].strip()[:-1], 16)
                if (
                    instruction is not None
                    and intermediate_datum is not None
                    and "jmp" not in instruction
                ):
                    intermediate_datum.next_addr.add(addr)
                instruction = line.split("\t")[2].strip()
                intermediate_datum = IntermediateData(
                    addr=addr,
                    next_addr=set(),
                    instruction=instruction,
                    node_start=node_start,
                )
                intermediate_data.append(intermediate_datum)
                addr_to_index[addr] = index
                jump_instructions = [
                    "jmp",
                    "jz",
                    "jnz",
                    "je",
                    "jne",
                    "ja",
                    "jb",
                    "jg",
                    "jl",
                    "jle",
                    "jge",
                    "jbe",
                    "jae",
                    "jp",
                    "jnp",
                    "js",
                    "jns",
                    "jc",
                    "jnc",
                    "jo",
                    "jno",
                    "jcxz",
                    "jecxz",
                    "jrcxz",
                ]
                node_start = any(
                    [jump in instruction.split("<")[0] for jump in jump_instructions]
                )
                if node_start:
                    jumps.append(index)
                index += 1

            for jump in jumps:
                addr_token = intermediate_data[jump].instruction.split()[1]
                if "%" in addr_token:
                    continue
                addr = int(addr_token, 16)
                if addr not in addr_to_index:
                    continue
                intermediate_data[addr_to_index[addr]].node_start = True
                intermediate_data[jump].next_addr.add(addr)

            function_data: List[List[IntermediateData]] = []
            basic_block_data: List[IntermediateData] = []
            for datum in intermediate_data:
                if datum.node_start:
                    if basic_block_data:
                        function_data.append(basic_block_data)
                    basic_block_data = [datum]
                else:
                    basic_block_data.append(datum)
            function_data.append(basic_block_data)

            data.append(
                self.__create_function_cfg(
                    function_name,
                    function_data,
                )
            )

        return data

    def __create_function_cfg(
        self,
        function_name: str,
        function_intermediate_data: List[List[IntermediateData]],
    ) -> FunctionCFG:
        head = None
        tail: Set[Node] = set()
        nodes: Dict[int, Node] = {}
        for basic_block_data in function_intermediate_data:
            basic_block = self.__create_basic_blocks(basic_block_data)
            if head is None:
                head = basic_block
                head.instrumented_addrs.add(head.addr)
            if "ret" in basic_block_data[-1].instruction:
                tail.add(basic_block)
            nodes[basic_block.addr] = basic_block
        function_cfg = FunctionCFG(function_name, head, tail, nodes)

        for addr, basic_block in function_cfg.nodes.items():
            for next_addr in basic_block.nexts:
                function_cfg.nodes[next_addr].prevs.add(addr)
        return function_cfg

    def __create_basic_blocks(self, intermediate_data: List[IntermediateData]) -> Node:
        return Node(
            addr=intermediate_data[0].addr,
            addrs=set([datum.addr for datum in intermediate_data]),
            prevs=set(),
            nexts=(
                intermediate_data[-1].next_addr
                if "ret" not in intermediate_data[-1].instruction
                else set()
            ),
            instrumented_addrs=(
                {intermediate_data[0].addr}
                if any(
                    "__TMC_END__" in datum.instruction for datum in intermediate_data
                )
                else set()
            ),
            lines=set(),
            reachable_instrumented_addrs=set(),
            addrs_reachable_without_any_instrumentation=set(),
            lines_from_addrs_reachable_wo_instrumentation=set(),
            fallback=False,
        )

    def __verify_cfg(self, cfg: List[FunctionCFG]) -> None:
        for function_cfg in cfg:
            self.__verify_function_cfg(function_cfg)

    def __verify_function_cfg(self, function_cfg: FunctionCFG):
        for block_addr, node in function_cfg.nodes.items():
            for next_addr in node.nexts:
                assert (
                    next_addr in function_cfg.nodes
                ), f"{hex(block_addr)} not in function_cfg.nodes"
            for prev_addr in node.prevs:
                assert (
                    prev_addr in function_cfg.nodes
                ), f"{hex(block_addr)} not in function_cfg.nodes"
        for tail in function_cfg.tail:
            assert (
                tail in function_cfg.nodes.values()
            ), f"{hex(tail.addr)} not in function_cfg.nodes"
            assert (
                len(tail.nexts) == 0
            ), f"{hex(tail.addr)} has nexts: {[hex(addr) for addr in tail.nexts]}"
        assert (
            function_cfg.head in function_cfg.nodes.values()
        ), f"{hex(function_cfg.head.addr)} not in function_cfg.nodes"
        assert (
            function_cfg.head.prevs == set()
        ), f"{hex(function_cfg.head.addr)} has prevs: {[hex(addr) for addr in function_cfg.head.prevs]}"

    def __add_line_nums_to_function_by_instruction(
        self,
        function_cfg_list: List[FunctionCFG],
    ) -> None:
        llvm_symbolier = LLVMSymbolizer(self.harness, self.llvm_symbolizer_path)
        for function_cfg in function_cfg_list:
            for _, basic_block in function_cfg.nodes.items():
                for addr in basic_block.addrs:
                    result = llvm_symbolier.run_llvm_symbolizer_addr(addr)
                    if result.error:
                        continue
                    line_num = result.line_number
                    basic_block.lines.add(
                        LineInfo(result.function_name, result.src_file, line_num)
                    )

    def __create_fallback_data(self, cfg: List[FunctionCFG]) -> Dict[int, Node]:
        data: Dict[int, Node] = {}
        for function_cfg in cfg:
            for _, node in function_cfg.nodes.items():
                if not node.instrumented_addrs:
                    continue
                fallback_node = node.copy()
                fallback_node.fallback = True
                for addr in node.instrumented_addrs:
                    data[addr] = fallback_node

        return data

    def __remove_self_loops(self, cfg: List[FunctionCFG]) -> None:
        for function_cfg in cfg:
            self.__remove_self_loop_in_function_cfg(function_cfg)

    def __remove_self_loop_in_function_cfg(self, function_cfg: FunctionCFG) -> None:
        for addr, node in function_cfg.nodes.items():
            if addr in node.nexts:
                node.nexts.remove(addr)
            if addr in node.prevs:
                node.prevs.remove(addr)

    def __simplify_cfg(self, function_cfg: FunctionCFG) -> None:
        while True:
            modified = False
            if self.__delete_single_entry_node(function_cfg):
                modified = True
                self.__remove_self_loop_in_function_cfg(function_cfg)
                if is_running_under_pytest():
                    self.__verify_function_cfg(function_cfg)
            if self.__delete_one_to_one_nodes(function_cfg):
                modified = True
                self.__remove_self_loop_in_function_cfg(function_cfg)
                if is_running_under_pytest():
                    self.__verify_function_cfg(function_cfg)
            if self.__delete_uninstrumented_nodes_nexts_instrumented(function_cfg):
                modified = True
                self.__remove_self_loop_in_function_cfg(function_cfg)
                if is_running_under_pytest():
                    self.__verify_function_cfg(function_cfg)

            if not modified:
                break

    def __delete_single_entry_node(self, function_cfg: FunctionCFG) -> bool:
        for addr, node in function_cfg.nodes.items():
            for next_addr in node.nexts:
                next_block = function_cfg.nodes[next_addr]
                if len(next_block.prevs) == 1 and all(
                    next_addr != tail.addr for tail in function_cfg.tail
                ):
                    if next_block.lines.issubset(node.lines):
                        # if is_running_under_pytest():
                        #     print(
                        #         f"__delete_single_entry_node {function_cfg.name}: delete {hex(next_addr)}, keep {hex(addr)}, head {hex(function_cfg.head.addr)}, tail {[hex(tail.addr) for tail in function_cfg.tail]}"
                        #     )
                        self.__merge_nodes(function_cfg, addr, next_addr, True)
                        return True
        return False

    def __delete_one_to_one_nodes(self, function_cfg: FunctionCFG) -> bool:
        for addr, node in function_cfg.nodes.items():
            if len(node.nexts) != 1:
                continue
            next_addr = next(iter(node.nexts))
            next_block = function_cfg.nodes[next_addr]
            if len(next_block.prevs) != 1:
                continue
            # if is_running_under_pytest():
            #     delete_addr = next_addr if node.instrumented_addrs else addr
            #     keep_addr = addr if node.instrumented_addrs else next_addr
            #     print(
            #         f"__delete_one_to_one_nodes {function_cfg.name}: delete {hex(delete_addr)}, keep {hex(keep_addr)}, head {hex(function_cfg.head.addr)}, tail {[hex(tail.addr) for tail in function_cfg.tail]}"
            #     )
            self.__merge_nodes(
                function_cfg,
                addr,
                next_addr,
                True if node.instrumented_addrs else False,
            )
            return True
        return False

    def __delete_uninstrumented_nodes_nexts_instrumented(
        self, function_cfg: FunctionCFG
    ) -> bool:
        for addr, node in function_cfg.nodes.items():
            if addr == function_cfg.head.addr:
                continue
            if node.instrumented_addrs or len(node.nexts) == 0:
                continue
            nexts_all_instrumented = True
            for next_addr in node.nexts:
                next_block = function_cfg.nodes[next_addr]
                if not next_block.instrumented_addrs:
                    nexts_all_instrumented = False
                    break
            if not nexts_all_instrumented:
                continue

            # if is_running_under_pytest():
            #     print(
            #         f"__delete_one_to_one_nodes {function_cfg.name}: delete {addr}, keep {[hex(addr) for addr in node.nexts]}, head {hex(function_cfg.head.addr)}, tail {[hex(tail.addr) for tail in function_cfg.tail]}"
            #     )

            function_cfg.nodes.pop(addr)

            for next_addr in node.nexts:
                next_block = function_cfg.nodes[next_addr]
                next_block.prevs.remove(addr)
                next_block.addrs.update(node.addrs)
                next_block.prevs.update(node.prevs)
                next_block.lines.update(node.lines)

            for prev_addr in node.prevs:
                function_cfg.nodes[prev_addr].nexts.remove(addr)
                function_cfg.nodes[prev_addr].nexts.update(node.nexts)

            return True
        return False

    def __merge_nodes(
        self,
        function_cfg: FunctionCFG,
        prev_addr: int,
        next_addr: int,
        merge_to_prev: bool,
    ) -> None:
        prev_node = (
            function_cfg.nodes[prev_addr]
            if merge_to_prev
            else function_cfg.nodes.pop(prev_addr)
        )
        prev_node.nexts.remove(next_addr)
        next_node = (
            function_cfg.nodes.pop(next_addr)
            if merge_to_prev
            else function_cfg.nodes[next_addr]
        )
        next_node.prevs.remove(prev_addr)

        node_to_delete = next_node if merge_to_prev else prev_node
        node_to_keep = prev_node if merge_to_prev else next_node

        for addr in node_to_delete.prevs:
            function_cfg.nodes[addr].nexts.remove(node_to_delete.addr)
            function_cfg.nodes[addr].nexts.add(node_to_keep.addr)

        for addr in node_to_delete.nexts:
            function_cfg.nodes[addr].prevs.remove(node_to_delete.addr)
            function_cfg.nodes[addr].prevs.add(node_to_keep.addr)

        node_to_keep.addrs.update(node_to_delete.addrs)
        node_to_keep.prevs.update(node_to_delete.prevs)
        node_to_keep.nexts.update(node_to_delete.nexts)
        node_to_keep.instrumented_addrs.update(node_to_delete.instrumented_addrs)
        node_to_keep.lines.update(node_to_delete.lines)

        if node_to_delete == function_cfg.head:
            function_cfg.head = node_to_keep
        if node_to_delete in function_cfg.tail:
            function_cfg.tail.remove(node_to_delete)
            function_cfg.tail.add(node_to_keep)

    def __reverse_traverse_cfg(self, function_cfg: FunctionCFG):
        for tail in function_cfg.tail:
            self.__reverse_traverse_cfg_helper(function_cfg, tail)

    def __reverse_traverse_cfg_helper(self, function_cfg: FunctionCFG, tail: Node):
        self.__reverse_traverse_cfg_helper_impl(
            function_cfg,
            tail,
            set(),
        )

    def __reverse_traverse_cfg_helper_impl(
        self,
        function_cfg: FunctionCFG,
        current_node: Node,
        visited_nodes: Set[int],
    ):
        if current_node.addr in visited_nodes:
            return

        if current_node.instrumented_addrs:
            current_node.addrs_reachable_without_any_instrumentation.update(
                visited_nodes
            )
            return

        visited_nodes.add(current_node.addr)
        for prev_addr in current_node.prevs:
            prev_node = function_cfg.nodes[prev_addr]
            self.__reverse_traverse_cfg_helper_impl(
                function_cfg, prev_node, visited_nodes
            )

    def __traverse_cfg(self, function_cfg: FunctionCFG):
        for _, node in function_cfg.nodes.items():
            if node.instrumented_addrs:
                self.__traverse_cfg_helper(function_cfg, node)

    def __traverse_cfg_helper(self, function_cfg: FunctionCFG, instrumented_node: Node):
        instrumented_visited: Set[int] = set()
        self.__traverse_cfg_helper_impl(
            function_cfg,
            instrumented_node,
            set(),
            instrumented_visited,
        )
        instrumented_visited.remove(instrumented_node.addr)
        instrumented_node.reachable_instrumented_addrs.update(instrumented_visited)

    def __traverse_cfg_helper_impl(
        self,
        function_cfg: FunctionCFG,
        current_node: Node,
        visited_nodes: Set[int],
        instrumented_visited: Set[int],
    ):
        if current_node.addr in visited_nodes:
            return
        if current_node.instrumented_addrs:
            instrumented_visited.add(current_node.addr)
        visited_nodes.add(current_node.addr)
        for next_addr in current_node.nexts:
            next_node = function_cfg.nodes[next_addr]
            self.__traverse_cfg_helper_impl(
                function_cfg, next_node, visited_nodes, instrumented_visited
            )

    def __create_data(
        self,
        cfg: List[FunctionCFG],
        fallback_data: Dict[int, Node],
        fallback_node_addrs: Set[int],
        simplify_failed_functions: Set[str],
    ) -> Dict[int, Node]:
        data: Dict[int, Node] = {}
        for function_cfg in cfg:
            if function_cfg.name in simplify_failed_functions:
                continue
            for _, node in function_cfg.nodes.items():
                for addr in node.instrumented_addrs:
                    data[addr] = node

        for addr in fallback_node_addrs:
            data[addr] = fallback_data[addr]
        return data

    def create_data(self) -> Dict[int, Node]:
        cfg = self.__run_objdump()
        if is_running_under_pytest():
            self.__verify_cfg(cfg)

        self.__add_line_nums_to_function_by_instruction(cfg)

        fallback_data = self.__create_fallback_data(cfg)
        fallback_node_addrs: Set[int] = set()
        simplify_failed_functions: Set[str] = set()

        self.__remove_self_loops(cfg)
        if is_running_under_pytest():
            self.__verify_cfg(cfg)

        for function_cfg in cfg:
            instrumented_addrs = [
                addr
                for addr, node in function_cfg.nodes.items()
                if node.instrumented_addrs
            ]
            try:
                self.__simplify_cfg(function_cfg)

                if len(function_cfg.nodes) > 1000:
                    raise Exception(
                        f"Too many nodes in {function_cfg.name}: {len(function_cfg.nodes)}"
                    )
            except Exception as e:
                fallback_node_addrs.update(instrumented_addrs)
                simplify_failed_functions.add(function_cfg.name)
                if is_running_under_pytest():
                    print(f"Failed to simplify {function_cfg.name}")
                    raise e

        if is_running_under_pytest():
            self.__verify_cfg(cfg)

        for function_cfg in cfg:
            if function_cfg.name in simplify_failed_functions:
                continue
            self.__reverse_traverse_cfg(function_cfg)
            self.__traverse_cfg(function_cfg)

            for _, node in function_cfg.nodes.items():
                if not node.instrumented_addrs:
                    continue
                for reachable_addr in node.addrs_reachable_without_any_instrumentation:
                    reachable_node = function_cfg.nodes[reachable_addr]
                    node.lines_from_addrs_reachable_wo_instrumentation.update(
                        reachable_node.lines
                    )
        return self.__create_data(
            cfg, fallback_data, fallback_node_addrs, simplify_failed_functions
        )


class CFGAnalyzer:
    def __init__(
        self, harness: str, llvm_symbolizer_path: str, redis_url: str, ncpu: int
    ) -> None:
        self.harness = harness
        self.llvm_symbolizer_path = llvm_symbolizer_path
        self.redis_url = redis_url
        self.ncpu = ncpu

        self.__create_data_in_parallel()

    def __create_data_in_parallel(self) -> None:
        self.data: Dict[int, Node] = {}
        try:
            workers = [
                CFGWorker(self.harness, self.llvm_symbolizer_path, i, self.ncpu)
                for i in range(self.ncpu)
            ]
            with multiprocessing.Pool(self.ncpu) as pool:
                result = pool.map_async(CFGWorker.create_data, workers)
                data = result.get(timeout=900)  # timeout in seconds

            for d in data:
                self.data.update(d)
        except Exception:
            pass

    def save_to_redis(self) -> None:
        parsed_url = urlparse(self.redis_url)
        redis_client = Redis(host=parsed_url.scheme, port=parsed_url.path)

        redis_key = f"{self.harness}"
        serialized_data = pickle.dumps(self.data)
        redis_client.set(redis_key, serialized_data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arguments for cfg analyzer")

    parser.add_argument(
        "--harness", type=str, required=True, help="Harness (must have)"
    )
    parser.add_argument(
        "--redis_url", type=str, required=True, help="Redis URL (must have)"
    )
    parser.add_argument(
        "--ncpu", type=int, required=True, help="Number of cores to use (must have)"
    )
    parser.add_argument(
        "--llvm_symbolizer",
        default="/out/llvm-symbolizer",
        help="Path to the symbolizer (default: /out/llvm-symbolizer)",
    )

    args = parser.parse_args()
    cfg_analyzer = CFGAnalyzer(
        args.harness, args.llvm_symbolizer, args.redis_url, args.ncpu
    )
    cfg_analyzer.save_to_redis()
