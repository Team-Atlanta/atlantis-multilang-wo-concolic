import importlib

from grammarinator.runtime import DefaultModel, RuleSize, simple_space_serializer
from grammarinator.tool import DefaultGeneratorFactory, GeneratorTool

from .image import generate_random_image


def generate_png(count=1) -> list[bytes]:
    return [generate_random_image("png") for _ in range(count)]


def generate_gif(count=1) -> list[bytes]:
    return [generate_random_image("gif") for _ in range(count)]


def generate_jpeg(count=1) -> list[bytes]:
    return [generate_random_image("jpeg") for _ in range(count)]


def generate_bmp(count=1) -> list[bytes]:
    return [generate_random_image("bmp") for _ in range(count)]


def import_dynamic(name: str):
    qualifier = name.rsplit(".", 1)
    if len(qualifier) != 2:
        return None

    return getattr(importlib.import_module(qualifier[0]), qualifier[1])


def generate_antlr4(generator_id: str, count=1) -> list[bytes]:
    if "." in generator_id:
        return []

    qualified_name = (
        f"customgen.generated.antlr4.{generator_id}Generator.{generator_id}Generator"
    )
    generator = import_dynamic(qualified_name)
    model = DefaultModel
    serializer = simple_space_serializer
    rule_size = RuleSize(512, 8192)  # type: ignore

    generated_outputs = []
    with GeneratorTool(
        generator_factory=DefaultGeneratorFactory(
            generator,
            model_class=model,
            listener_classes=[],
        ),
        out_format="",
        limit=rule_size,
        errors="ignore",
        serializer=serializer,
        cleanup=False,
    ) as generator_tool:
        for _ in range(count):
            for _ in range(5):
                try:
                    generated_outputs.append(generator_tool.create_bytes())
                except Exception as _e:
                    continue
                break

    return generated_outputs
