from typing import Any, Dict, List

from langgraph.graph import StateGraph
from pydantic import BaseModel


# 1. Define LangGraph State
class WorkflowState(BaseModel):
    user_input: str
    records: List[Dict[str, Any]]
    current_variable: Dict[str, Any] = {}  # Explicitly set as Dict[str, Any]


# 2. Node that modifies specific index value
def modify_current_variable(state: WorkflowState) -> WorkflowState:
    """Check if state.records is affected after modifying current_variable"""
    print(f"Before Modification -> state.records[1]: {state.records[1]}")

    # Copy and modify specific element
    current_variable = state.records[1].copy()
    current_variable[str(1234)] = 1234  # Convert integer key to string

    # Create new state, don't copy existing records
    new_state = WorkflowState(
        user_input=state.user_input,
        records=state.records,  # Keep existing records as is
        current_variable=current_variable,
    )

    print(f"After Modification -> current_variable: {new_state.current_variable}")
    print(
        f"After Modification -> state.records[1]: {state.records[1]}"
    )  # Original records should not be modified!

    return new_state


# 3. Create LangGraph workflow
workflow = StateGraph(WorkflowState)

workflow.add_node("modify_current_variable", modify_current_variable)
workflow.set_entry_point("modify_current_variable")

# 4. Execute
app = workflow.compile()
state = WorkflowState(
    user_input="Hello",
    records=[
        {"id": 1, "status": "pending"},
        {"id": 2, "status": "pending"},  # This will be modified
        {"id": 3, "status": "pending"},
    ],
)

# 5. Execute and check results
for output in app.stream(state):
    print("Final Output:", output)
