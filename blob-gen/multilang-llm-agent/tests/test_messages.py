import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from mlla.utils.messages import add_cache_control

# All of the Message classes you want to test
message_classes = [HumanMessage, AIMessage, SystemMessage]

# (name, input_content, expected_transformed_content)
test_cases = [
    (
        "str",
        "Hello, world!",
        [
            {
                "text": "Hello, world!",
                "type": "text",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ],
    ),
    (
        "list_str",
        ["foo", "bar"],
        [
            {"text": "foo", "type": "text"},
            {
                "text": "bar",
                "type": "text",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ],
    ),
    (
        "list_dict",
        [{"text": "x", "type": "text"}, {"text": "y", "type": "text"}],
        [
            {"text": "x", "type": "text"},
            {
                "text": "y",
                "type": "text",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ],
    ),
    (
        "list_dict_cached",
        [
            {
                "text": "x",
                "type": "text",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
            {"text": "y", "type": "text"},
        ],
        [
            {
                "text": "x",
                "type": "text",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
            {"text": "y", "type": "text"},
        ],
    ),
]


@pytest.mark.parametrize("MessageCls", message_classes)
@pytest.mark.parametrize(
    "case_name, content, expected",
    test_cases,
    ids=[c[0] for c in test_cases],  # type: ignore
)
def test_add_cache_control__all_variants(MessageCls, case_name, content, expected):
    msg = MessageCls(content=content)
    out = add_cache_control(msg)

    assert msg == out, f"Case {case_name} failed"

    assert msg.content == expected, f"Case {case_name} failed"
    if isinstance(content, list):
        assert len(msg.content) == len(content), f"Case {case_name} failed"

    assert out.content == expected, f"Case {case_name} failed"
    if isinstance(content, list):
        assert len(out.content) == len(content), f"Case {case_name} failed"
