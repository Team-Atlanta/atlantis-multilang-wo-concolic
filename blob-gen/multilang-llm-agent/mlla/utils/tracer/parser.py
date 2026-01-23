import json

from .model import Relation, RelationsList


class TracerParser:
    def __init__(self, data: list[str]) -> None:
        self.data = data

    def parse(self) -> RelationsList:
        relations_set = set()
        for data in self.data:
            json_dict = json.loads(data)

            if not isinstance(json_dict, dict):
                raise ValueError(f"Invalid trace data: {data}")

            if "call_info" not in json_dict:
                continue

            _relation = json_dict["call_info"]
            relation = Relation.model_validate(_relation)
            relation.callees = list(set(relation.callees))
            relations_set.add(relation)

        return RelationsList(list(relations_set))
