from abc import ABC, abstractmethod
from functools import partial
from pathlib import Path
from typing import Literal, Optional

from langchain_community.agent_toolkits import FileManagementToolkit
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import StateSnapshot
from loguru import logger

from .agent_context import set_agent_instance_context
from .constants import AutoPromptChoice
from .context import GlobalContext
from .llm import LLM, PrioritizedTool
from .llm_tools.astgrep import create_ag_tools
from .llm_tools.read_file import create_read_file_tool
from .llm_tools.ripgrep import create_rg_tool

BCDA = "bcda"
CPUA = "cpua"
EA = "ea"
MCGA = "mcga"
SANUA = "sanua"
BCGA = "bcga"
CGPA = "cgparser_agent"
MUTATOR_AGENT = "mutator"
GENERATOR_AGENT = "generator"
BLOBGEN_AGENT = "blobgen"
ORCHESTRATOR_AGENT = "orchestrator"

TOOL_NODE = "llm_tools"
TOOL_MODEL = "call_model_with_tools"

# Put top-level agents that need to aggregate all internal LLM usage.
# For these agents, we will take a snapshot and use that.
SNAPSHOT_AGENTS = [CPUA]


class ExpectedException(Exception):
    """Expected exception that is expected to be handled by the agent"""

    pass


class BaseAgentTemplate(ABC):
    builder: StateGraph
    gc: GlobalContext
    # checkpointer: RedisSaver
    cur_state: StateSnapshot | None = None
    ret_file: Path
    prev_ret_file: Path | None
    module_name: str
    output_state: dict | MessagesState
    file_system_tool_node: ToolNode
    entire_tools: list[PrioritizedTool]
    entire_tool_node: ToolNode
    llm_with_tools: Optional[LLM] = None

    def __init__(
        self,
        gc: GlobalContext,
        ret_dir: Path,
        input_state,
        output_state,
        overall_state=None,
        tools: list[PrioritizedTool] = [],
        step_mapper: dict[int, str] = {},
        enable_usage_snapshot: bool = True,
        llm_with_tools: str | None = None,
    ):
        self.gc = gc
        self.module_name = ret_dir.name
        self.enable_usage_snapshot = enable_usage_snapshot

        # Generate unique instance ID using object ID
        self.instance_id = f"{self.module_name}_{id(self)}"

        # Set the agent instance context for all subsequent operations
        set_agent_instance_context(self.instance_id)

        self.file_system_tools = FileManagementToolkit(
            # root_dir=str(gc.cp.cp_src_path),
            selected_tools=["list_directory", "file_search"],
        ).get_tools()

        self.file_system_tool_node = ToolNode(self.file_system_tools)

        if tools:
            self.entire_tools = tools
            _entire_tools = [tool.get_tool() for tool in self.entire_tools]
            self.entire_tool_node = ToolNode(_entire_tools)
        else:
            tools = []
            rg_tool = create_rg_tool(is_dev=gc.is_dev)
            if rg_tool:
                tools.append(rg_tool)
            if self.__class__.__name__ == "CGParserAgent":
                from .llm_tools.codeindexer import create_ci_tool

                tools.append(create_ci_tool(gc))
            else:
                from .llm_tools.cgparser import create_cg_parser_tool

                tools.append(create_cg_parser_tool(gc))

            tools += create_ag_tools()
            tools.append(create_read_file_tool())
            tools += PrioritizedTool.from_tools(self.file_system_tools, 1)

            self.entire_tools = tools
            _entire_tools = [tool.get_tool() for tool in self.entire_tools]
            self.entire_tool_node = ToolNode(_entire_tools)

        self.builder = StateGraph(overall_state, input=input_state, output=output_state)
        self.builder.add_node(TOOL_NODE, self.entire_tool_node)
        self.builder.add_node(
            TOOL_MODEL, partial(self.call_model_with_tools, tools=self.entire_tools)
        )
        self.builder.add_node("load_previous_result", self.load_previous_result)
        self.builder.add_node("preprocess", self.preprocess)
        self.builder.add_node("finalize", self.finalize)

        # Graph structure with conditional snapshot support
        self.builder.add_node("_debug_snapshot_start", self._debug_snapshot_start)
        self.builder.add_node("_debug_snapshot_end", self._debug_snapshot_end)

        # START -> _debug_snapshot_start -> (conditional) -> ...
        self.builder.add_edge(START, "_debug_snapshot_start")
        self.builder.add_conditional_edges(
            "_debug_snapshot_start",
            self.need_loading_prev_state,
            ["preprocess", "load_previous_result"],
        )

        self.builder.add_edge(TOOL_NODE, TOOL_MODEL)

        after_tool = partial(self.after_tool, step_mapper=step_mapper)
        self.builder.add_conditional_edges(TOOL_MODEL, after_tool)

        self.builder.add_edge("load_previous_result", END)

        # finalize -> _debug_snapshot_end -> END
        self.builder.add_edge("finalize", "_debug_snapshot_end")
        self.builder.add_edge("_debug_snapshot_end", END)

        self.output_state = output_state

        ret_dir.mkdir(parents=True, exist_ok=True)

        try:
            date_pattern = "????-??-??_??-??-??*"
            self.prev_ret_file = max(ret_dir.glob(date_pattern), key=lambda x: x.name)
        except ValueError:
            self.prev_ret_file = None

        self.ret_file = ret_dir / f"{gc.timestamp}.json"

        if llm_with_tools:
            self.llm_with_tools = LLM(
                model=llm_with_tools,
                config=self.gc,
                tools=self.entire_tools,
                agent_name=self.module_name,
            )

    def after_tool(self, state, step_mapper: dict[int, str]) -> str:
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return TOOL_NODE

        if "step" in state and state["step"] in step_mapper:
            return step_mapper[state["step"]]
        else:
            return "finalize"

    def _debug_snapshot_start(self, state):
        """Create start snapshot for usage tracking."""
        # Use instance_id instead of module_name for snapshots
        self.gc.general_callback.create_snapshot(f"{self.instance_id}_start")
        return {}

    def _debug_snapshot_end(self, state):
        """Create end snapshot and log usage metrics."""
        # Use instance_id instead of module_name for snapshots
        self.gc.general_callback.create_snapshot(f"{self.instance_id}_end")
        if self.enable_usage_snapshot:
            self.log_agent_metrics()
        return {}

    def log_agent_metrics(self) -> None:
        """Log metrics for this agent using snapshot diff."""
        # Get snapshot usage data for this instance
        usage = self.gc.general_callback.get_usage_between_snapshots(
            f"{self.instance_id}_start", f"{self.instance_id}_end"
        )

        # Print header
        logger.info(f"Agent {self.instance_id} metrics:")

        # Always print snapshot metrics
        if usage:
            logger.info(f"Instance Snapshot Metrics: {usage}")
        else:
            logger.warning(f"No snapshot usage data available for {self.instance_id}")

        # Only print historical metrics for non-snapshot agents
        if self.module_name not in SNAPSHOT_AGENTS:
            agent_usage = self.gc.general_callback.get_agent_usage(self.module_name)
            if agent_usage:
                logger.info(
                    f"Historical Agent Metrics for {self.module_name}: {agent_usage}"
                )
            else:
                logger.warning(
                    f"No historical usage data available for {self.module_name}"
                )
        else:
            logger.info(
                f"Agent {self.module_name} uses snapshot-based metrics (no historical"
                " data needed)"
            )

    def call_model_with_tools(self, state: dict, tools: list[PrioritizedTool] = []):
        # logger.debug(
        #     f"{state['cur_function']}, len(messages) : {len(state['messages'])}"
        # )

        messages = state["messages"]

        # Validate that messages is not empty
        if not messages:
            logger.error("Empty messages list passed to call_model_with_tools")
            # Just keep going? as invoke and ainvoke in llm.py can handle this?
            # raise ValueError("Cannot invoke LLM with empty messages list")

        choice = [AutoPromptChoice.COT]

        if len(tools) == 0:
            tools = PrioritizedTool.from_tools(self.file_system_tools, 1)

        # TODO: enable this after solving existing issues
        # model = "atlanta-tool"
        # TODO: gemini seems to be not working
        # model = "gemini-2.0-flash-exp"
        model = "gpt-4.1"

        if self.llm_with_tools:
            model_with_tools = self.llm_with_tools
        else:
            model_with_tools = LLM(
                model=model,
                config=self.gc,
                tools=tools,
            )

        responses = model_with_tools.invoke(
            messages,
            choice=choice,
        )

        state["messages"] = responses

        return state

    def call_model_with_structured_output(
        self, state: dict, output_format=None, model="gpt-4.1"
    ):
        messages = state["messages"]

        model_with_structured_output = LLM(
            model=model,
            config=self.gc,
            output_format=output_format,
        )

        responses = model_with_structured_output.invoke(
            messages,
        )

        state["messages"] = responses

        return state

    @abstractmethod
    def deserialize(self, state, content: str) -> dict:
        pass

    @abstractmethod
    def serialize(self, state) -> str:
        pass

    @abstractmethod
    def preprocess(self, state):
        pass

    @abstractmethod
    def finalize(self, state):
        pass

    def need_loading_prev_state(
        self, _state
    ) -> Literal["preprocess", "load_previous_result"]:
        if self.module_name in self.gc.load_agent_names:
            logger.debug(
                f"Module {self.module_name} in load_agent_names: "
                f"{self.module_name in self.gc.load_agent_names}, "
                f"Previous result file: {self.prev_ret_file}"
            )
        if self.module_name in self.gc.load_agent_names and self.prev_ret_file:
            return "load_previous_result"
        return "preprocess"

    def load_previous_result(self, state):
        new_state = self.output_state(state)

        de_state = self.deserialize(state, self.prev_ret_file.read_text())
        new_state.update(de_state)

        logger.debug(f"load previous result from {self.prev_ret_file}")

        return new_state

    def compile(self):
        graph = self.builder.compile()
        return graph

    def get_next_state(self):
        cur_state = self.cur_state
        self.cur_state = cur_state.next
        return cur_state
