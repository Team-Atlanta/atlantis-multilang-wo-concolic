class BugInfo:
    id: str
    type: str
    description: str
    examples: list
    sink_functions: list

    def __init__(self, id: str, type: str, desc: str):
        self.id = id
        self.type = type
        self.description = desc
        self.examples = []
        self.sink_functions = []
