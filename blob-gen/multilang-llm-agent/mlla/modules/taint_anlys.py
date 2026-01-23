from typing_extensions import List, TypedDict


class TaintAnalysisState(TypedDict):
    # interesting paths (candidate paths)
    paths: List[str]
    input_sources: List[str]
    # possible sink functions
    vuln_sink_functions: List[str]


class TaintAnalysisOutputState(TypedDict):
    # from input source to sink functions
    paths: List[str]


def taint_anlysis(state):
    pass
