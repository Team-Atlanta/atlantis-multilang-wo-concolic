import pytest

from mlla.utils.bedrock_callback import (
    calculate_cache_savings,
    calculate_full_token_cost,
    calculate_token_cost,
)

CLAUDE_MODEL = "claude-3-7-sonnet-20250219"
GPT4O_MODEL = "gpt-4o"


def _create_token_usage(input_tokens, output_tokens, cache_read=0, cache_creation=0):
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if cache_read or cache_creation:
        usage["input_token_details"] = {
            "cache_read": cache_read,
            "cache_creation": cache_creation,
        }
    else:
        usage["input_token_details"] = {}
    return usage


def test_claude_regular_cost():
    token_usage = _create_token_usage(1000, 500)
    expected_cost = 0.0105
    result = calculate_token_cost(token_usage, CLAUDE_MODEL)
    assert result == pytest.approx(expected_cost)


def test_claude_with_cache_read():
    token_usage = _create_token_usage(1000, 500, cache_read=600)
    regular = (1000 - 600) * 0.000003
    output = 500 * 0.000015
    cache_read = 600 * 0.0000003
    expected_cost = regular + output + cache_read
    result = calculate_token_cost(token_usage, CLAUDE_MODEL)
    assert result == pytest.approx(expected_cost)


def test_claude_with_cache_creation():
    token_usage = _create_token_usage(1000, 500, cache_creation=800)
    # Regular input: (1000 - 800) * 0.000003 = 0.0006
    # Output: 500 * 0.000015 = 0.0075
    # Cache creation (1h): 800 * 0.000006 = 0.0048
    # Total: 0.0006 + 0.0075 + 0.0048 = 0.0129
    expected_cost = 0.0129
    result = calculate_token_cost(token_usage, CLAUDE_MODEL)
    assert result == pytest.approx(expected_cost)


def test_claude_with_both_cache_types():
    token_usage = _create_token_usage(1000, 500, cache_read=300, cache_creation=400)
    # Regular input: (1000 - 300 - 400) * 0.000003 = 0.0009
    # Output: 500 * 0.000015 = 0.0075
    # Cache read: 300 * 0.0000003 = 0.00009
    # Cache creation (1h): 400 * 0.000006 = 0.0024
    # Total: 0.0009 + 0.0075 + 0.00009 + 0.0024 = 0.01089
    expected_cost = 0.01089
    result = calculate_token_cost(token_usage, CLAUDE_MODEL)
    assert result == pytest.approx(expected_cost)


def test_claude_real_example():
    token_usage = {
        "input_tokens": 3128,
        "output_tokens": 1688,
        "total_tokens": 4816,
        "input_token_details": {"cache_read": 2183, "cache_creation": 0},
    }
    expected_cost = 0.0288099
    result = calculate_token_cost(token_usage, CLAUDE_MODEL)
    assert result == pytest.approx(expected_cost)


def test_gpt4o_regular():
    token_usage = _create_token_usage(1000, 500)
    expected_cost = 0.0075
    result = calculate_token_cost(token_usage, GPT4O_MODEL)
    assert result == pytest.approx(expected_cost)


def test_gpt4o_with_cache():
    token_usage = _create_token_usage(1000, 500, cache_read=600)
    expected_cost = 0.00675
    result = calculate_token_cost(token_usage, GPT4O_MODEL)
    assert result == pytest.approx(expected_cost)


def test_unknown_model():
    token_usage = {"input_tokens": 1000, "output_tokens": 500}
    result = calculate_token_cost(token_usage, "unknown-model")
    assert result == 0


def test_zero_tokens():
    token_usage = {"input_tokens": 0, "output_tokens": 0}
    result = calculate_token_cost(token_usage, CLAUDE_MODEL)
    assert result == 0


def _test_cache_savings(
    prompt_tokens, completion_tokens, model_id, cache_read=0, cache_creation=0
):
    cost_without_cache = calculate_full_token_cost(
        prompt_tokens, completion_tokens, model_id
    )
    token_usage = _create_token_usage(
        prompt_tokens, completion_tokens, cache_read, cache_creation
    )
    actual_cost = calculate_token_cost(token_usage, model_id)
    expected_savings = cost_without_cache - actual_cost

    result = calculate_cache_savings(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model_id=model_id,
        actual_cost=actual_cost,
        token_usage=token_usage,
    )

    assert result == pytest.approx(expected_savings)


def test_calculate_cache_savings_with_cache_read():
    _test_cache_savings(1000, 500, CLAUDE_MODEL, cache_read=600)


def test_calculate_cache_savings_with_cache_creation():
    _test_cache_savings(1000, 500, CLAUDE_MODEL, cache_creation=800)


def test_calculate_cache_savings_with_both_cache_types():
    _test_cache_savings(1000, 500, CLAUDE_MODEL, cache_read=300, cache_creation=400)


def test_calculate_cache_savings_with_no_cache():
    result = calculate_cache_savings(
        prompt_tokens=1000,
        completion_tokens=500,
        model_id=CLAUDE_MODEL,
        actual_cost=calculate_full_token_cost(1000, 500, CLAUDE_MODEL),
        token_usage=_create_token_usage(1000, 500),
    )
    assert result == 0


def test_calculate_cache_savings_with_unknown_model():
    token_usage = _create_token_usage(1000, 500, cache_read=300, cache_creation=400)
    result = calculate_cache_savings(
        prompt_tokens=1000,
        completion_tokens=500,
        model_id="unknown-model",
        actual_cost=0.0,
        token_usage=token_usage,
    )
    assert result == 0
