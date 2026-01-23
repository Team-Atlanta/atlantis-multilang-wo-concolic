from abc import ABC, abstractmethod
import codecs

class Encoder(ABC):
    @abstractmethod
    def encode(self, input: bytes) -> bytes:
        pass

    @abstractmethod
    def validate(self, input: bytes):
        pass

    @classmethod
    def __subclasshook__(cls, subclass: type) -> bool:
        if cls is Encoder:
            methods = [method for c in subclass.__mro__ for method in c.__dict__]
            if "encode" in methods and "validate" in methods:
                return True
        return NotImplemented

class Base64Encoder(Encoder):
    def encode(self, input: bytes) -> bytes:
        return codecs.encode(input, "base64")

class Generator(ABC):
    @abstractmethod
    def generate(self) -> bytes:
        pass

    @abstractmethod
    def validate(self, input: bytes):
        pass

    @classmethod
    def __subclasshook__(cls, subclass: type) -> bool:
        if cls is Generator:
            methods = [method for c in subclass.__mro__ for method in c.__dict__]
            if "generate" in methods and "validate" in methods:
                return True
        return NotImplemented
