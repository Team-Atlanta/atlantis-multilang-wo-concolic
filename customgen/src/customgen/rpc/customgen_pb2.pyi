from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CustomGenRequest(_message.Message):
    __slots__ = ("generator_id", "count")
    GENERATOR_ID_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    generator_id: str
    count: int
    def __init__(self, generator_id: _Optional[str] = ..., count: _Optional[int] = ...) -> None: ...

class GenerationResult(_message.Message):
    __slots__ = ("count", "output")
    COUNT_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    count: int
    output: _containers.RepeatedScalarFieldContainer[bytes]
    def __init__(self, count: _Optional[int] = ..., output: _Optional[_Iterable[bytes]] = ...) -> None: ...

class GenerationError(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: str
    def __init__(self, message: _Optional[str] = ...) -> None: ...

class CustomGenResponse(_message.Message):
    __slots__ = ("generated", "failed")
    GENERATED_FIELD_NUMBER: _ClassVar[int]
    FAILED_FIELD_NUMBER: _ClassVar[int]
    generated: GenerationResult
    failed: GenerationError
    def __init__(self, generated: _Optional[_Union[GenerationResult, _Mapping]] = ..., failed: _Optional[_Union[GenerationError, _Mapping]] = ...) -> None: ...
