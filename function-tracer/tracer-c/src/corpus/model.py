from pydantic import BaseModel


class Corpus(BaseModel):
    harness: str
    data: bytes
    path: str
    corpus_id: str
