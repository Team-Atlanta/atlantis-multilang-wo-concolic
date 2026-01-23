import getpass
import os
import random
import string
import time
from typing import List

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

load_dotenv(".env.secret")

KEY = (
    getpass.getpass("Enter your LiteLLM API key: ").strip()
    if os.getenv("LITELLM_KEY") is None
    else os.getenv("LITELLM_KEY")
)

URL = (
    input("Enter your LiteLLM URL: ").strip()
    if os.getenv("LITELLM_URL") is None
    else os.getenv("LITELLM_URL")
)

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=KEY,
    base_url=URL,
    betas=["extended-cache-ttl-2025-04-11"],
)


def generate_random_content(base_text: str, length: int = 1000) -> str:
    """Generate random content based on base text with random elements"""
    # Generate random words
    random_words = []
    word_templates = [
        "technology",
        "framework",
        "programming",
        "development",
        "software",
        "application",
        "system",
        "database",
        "algorithm",
        "interface",
        "architecture",
        "implementation",
        "optimization",
        "performance",
        "security",
        "integration",
        "deployment",
        "testing",
        "debugging",
        "maintenance",
    ]

    for _ in range(50):
        word = random.choice(word_templates)
        # Add random suffix to make it unique
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
        random_words.append(f"{word}_{suffix}")

    # Generate random sentences
    random_sentences = []
    for i in range(20):
        sentence_words = random.sample(random_words, random.randint(5, 12))
        sentence = (
            f"The {' '.join(sentence_words)} provides excellent functionality for"
            " modern applications."
        )
        random_sentences.append(sentence)

    # Combine base text with random content
    random_id = "".join(random.choices(string.ascii_letters + string.digits, k=10))
    content = f"{base_text}\n\nRandom Session ID: {random_id}\n\n"
    content += "\n".join(random_sentences)

    # Pad to desired length
    while len(content) < length:
        content += f" Additional random content {random.randint(1000, 9999)}."

    return content[:length]


def print_usage_analysis(usage_metadata, step_name: str):
    """Print detailed usage analysis"""
    print(f"\n=== {step_name} ===")
    print(usage_metadata)
    print(f"Input tokens: {usage_metadata.get('input_tokens', 0)}")
    print(f"Output tokens: {usage_metadata.get('output_tokens', 0)}")
    print(f"Total tokens: {usage_metadata.get('total_tokens', 0)}")

    # Extract cache details from input_token_details
    input_token_details = usage_metadata.get("input_token_details", {})
    cache_read = input_token_details.get("cache_read", 0)
    cache_creation = input_token_details.get("cache_creation", 0)

    print(f"Cache creation tokens: {cache_creation}")
    print(f"Cache read tokens: {cache_read}")

    # Calculate cache efficiency
    input_tokens = usage_metadata.get("input_tokens", 0)

    if cache_read > 0:
        print(f"✅ Cache HIT: {cache_read} tokens read from cache")
    if cache_creation > 0:
        print(f"🔄 Cache CREATION: {cache_creation} tokens added to cache")
    if input_tokens > 0 and cache_read == 0 and cache_creation == 0:
        print(f"❌ No cache activity: {input_tokens} tokens processed normally")


def initialize_test_content():
    """Initialize content for the entire test run"""
    return {
        "content_1": generate_random_content(
            "You are a technology expert specializing in programming languages and"
            " frameworks.",
            5000,
        ),
        "content_2": generate_random_content(
            "Here's the LangChain documentation and framework details:", 2000
        ),
        "content_3": generate_random_content(
            "Here's the Python documentation and language specifications:", 2000
        ),
        "content_4": generate_random_content(
            "Please analyze both technologies and provide detailed insights.", 2000
        ),
        "content_5": generate_random_content(
            "Additional context about software architecture patterns:", 2000
        ),
        "content_6": generate_random_content(
            "Information about database design and optimization:", 2000
        ),
        "content_7": generate_random_content(
            "Final analysis and recommendations for implementation:", 2000
        ),
    }


# Global content that stays the same within a test run but changes between runs
GLOBAL_CONTENT = initialize_test_content()


def create_messages_with_count_and_cache(
    message_count: int, cache_last: bool = False
) -> List[BaseMessage]:
    """Create messages with specified count and optionally cache the last message"""

    if message_count < 1 or message_count > 7:
        raise ValueError("Message count must be between 1 and 7")

    # Anthropic requires at least one HumanMessage, so we need at least 2 messages total
    # If message_count is 1, we'll create a system message + human message
    actual_message_count = max(2, message_count)

    messages = []

    for i in range(actual_message_count):
        content_key = f"content_{i+1}"  # Content keys are 1-based

        # Create content block
        block = {
            "type": "text",
            "text": GLOBAL_CONTENT[content_key],
        }

        # Add cache control to the last message if requested (using 0-based index)
        if cache_last and i == message_count - 1:
            block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}

        # Create the appropriate message type
        if i == 0:
            messages.append(SystemMessage(content=[block]))
        else:
            messages.append(HumanMessage(content=[block]))

    # Debug: Print message structure and cache info
    print(
        f"DEBUG: Created {len(messages)} messages (requested: {message_count}, actual:"
        f" {actual_message_count})"
    )
    for j, msg in enumerate(messages):
        has_cache = any(
            "cache_control" in block for block in msg.content if isinstance(block, dict)
        )
        cache_info = " (CACHED)" if has_cache else ""
        print(f"  Message index {j}: {type(msg).__name__}{cache_info}")

    return messages


def test_gradual_increase_with_last_cache():
    """
    Test 1: Gradually increase messages from 1 to 7, caching the last message each time
    """
    print("\n" + "=" * 80)
    print("TEST 1: GRADUAL INCREASE WITH LAST MESSAGE CACHING")
    print("=" * 80)

    results = []

    for i in range(1, 8):  # 1 to 7 messages
        print(f"\n--- Step {i}: {i} message(s) with last message cached ---")
        messages = create_messages_with_count_and_cache(i, cache_last=True)
        response = llm.invoke(messages)
        print_usage_analysis(
            response.usage_metadata, f"Step {i} - {i} messages, last cached"
        )
        results.append(response.usage_metadata)

        time.sleep(1)  # Brief pause between requests

    return results


def test_revisit_all_subsets():
    """
    Test 2: After building up to 7 messages,
    revisit each subset (1, 1-2, 1-3, 1-4, etc.)
    to check if caching works for all previously cached content
    """
    print("\n" + "=" * 80)
    print("TEST 2: REVISIT ALL SUBSETS TO CHECK CACHE HITS")
    print("=" * 80)

    results = []

    for i in range(1, 8):  # 1 to 7 messages
        print(
            f"\n--- Revisit: First {i} message(s) (with cache flag to trigger cache"
            " lookup) ---"
        )
        messages = create_messages_with_count_and_cache(i, cache_last=True)
        response = llm.invoke(messages)
        print_usage_analysis(
            response.usage_metadata, f"Revisit {i} - First {i} messages with cache"
        )
        results.append(response.usage_metadata)

        time.sleep(1)  # Brief pause between requests

    return results


def test_multiple_vs_single_cache_points():
    """
    Test 3: Compare multiple cache points vs single cache point
    """
    print("\n" + "=" * 80)
    print("TEST 3: MULTIPLE VS SINGLE CACHE POINTS COMPARISON")
    print("=" * 80)

    results = []

    # Scenario A: 7 messages with 4 cache points at 2, 3, 4, 5 (indices 1, 2, 3, 4)
    print(
        "\n--- Scenario A: 7 messages with 4 cache points at positions 2, 3, 4, 5 ---"
    )

    content_blocks_a = []
    for i in range(7):
        content_key = f"content_{i+1}"
        block = {
            "type": "text",
            "text": GLOBAL_CONTENT[content_key],
        }

        # Add cache control to positions 2, 3, 4, 5 (indices 1, 2, 3, 4)
        if i in [1, 2, 3, 4]:  # positions 2, 3, 4, 5
            block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}

        content_blocks_a.append(block)

    messages_a = [SystemMessage(content=[content_blocks_a[0]])]
    for block in content_blocks_a[1:]:
        messages_a.append(HumanMessage(content=[block]))

    print(
        "DEBUG: Scenario A - Cache points at indices 1, 2, 3, 4 (positions 2, 3, 4, 5)"
    )
    response_a = llm.invoke(messages_a)
    print(response_a.response_metadata)
    print_usage_analysis(response_a.usage_metadata, "Scenario A - 4 cache points")
    results.append(("Scenario A (4 cache points)", response_a.usage_metadata))

    # Scenario B: 7 messages with 1 cache point at position 4 (index 3)
    print("\n--- Scenario B: 7 messages with 1 cache point at position 4 ---")

    content_blocks_b = []
    for i in range(7):
        content_key = f"content_{i+1}"
        block = {
            "type": "text",
            "text": GLOBAL_CONTENT[content_key],
        }

        # Add cache control only to position 4 (index 3)
        if i == 3:  # position 4
            block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}

        content_blocks_b.append(block)

    messages_b = [SystemMessage(content=[content_blocks_b[0]])]
    for block in content_blocks_b[1:]:
        messages_b.append(HumanMessage(content=[block]))

    print("DEBUG: Scenario B - Cache point at index 3 (position 4)")
    response_b = llm.invoke(messages_b)
    print(response_b.response_metadata)
    print_usage_analysis(response_b.usage_metadata, "Scenario B - 1 cache point")
    results.append(("Scenario B (1 cache point)", response_b.usage_metadata))

    # Scenario B: 7 messages with 1 cache point at position 4 (index 3)
    print("\n--- Scenario B: 7 messages with 1 cache point at position 4 ---")

    content_blocks_b = []
    for i in range(7):
        content_key = f"content_{i+1}"
        block = {
            "type": "text",
            "text": GLOBAL_CONTENT[content_key],
        }

        # Add cache control only to position 3 (index 2)
        if i == 2:  # position 3
            block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}

        content_blocks_b.append(block)

    messages_b = [SystemMessage(content=[content_blocks_b[0]])]
    for block in content_blocks_b[1:]:
        messages_b.append(HumanMessage(content=[block]))

    print("DEBUG: Scenario B - Cache point at index 2 (position 3)")
    response_b = llm.invoke(messages_b)
    print(response_b.response_metadata)
    print_usage_analysis(response_b.usage_metadata, "Scenario B - 1 cache point")
    results.append(("Scenario B (1 cache point)", response_b.usage_metadata))

    # time.sleep(2)

    return results


def test_cache_limit_behavior():
    """
    Test 4: Test what happens when we exceed the 4 cache point limit
    """
    print("\n" + "=" * 80)
    print("TEST 4: CACHE LIMIT BEHAVIOR (Beyond 4 cache points)")
    print("=" * 80)

    # Try to cache multiple points beyond the 4-point limit
    print("\n--- Testing with 5 cache points (should hit limit) ---")

    # Create messages with cache points at positions 3, 4, 5, 6, 7 (5 cache points)
    content_blocks = []
    for i in range(7):
        content_key = f"content_{i+1}"
        block = {
            "type": "text",
            "text": GLOBAL_CONTENT[content_key],
        }

        # Add cache control to positions 3, 4, 5, 6, 7 (5 cache points total)
        if i >= 2:  # positions 3, 4, 5, 6, 7
            block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}

        content_blocks.append(block)

    messages = [SystemMessage(content=[content_blocks[0]])]
    for block in content_blocks[1:]:
        messages.append(HumanMessage(content=[block]))

    response = llm.invoke(messages)
    print_usage_analysis(response.usage_metadata, "5 cache points test")

    return response.usage_metadata


def main():
    """Run all cache tests"""
    print("Starting Anthropic Cache Testing - Comprehensive Cache Behavior Analysis")
    print(
        "This will test multiple caching scenarios and analyze cache behavior patterns"
    )

    try:
        # Test 1: Gradual increase with last message caching
        test_gradual_increase_with_last_cache()

        # Test 2: Revisit all subsets to check cache hits
        test_revisit_all_subsets()

        # Test 3: Multiple vs single cache points comparison
        test_multiple_vs_single_cache_points()

        # Test 4: Cache limit behavior
        test_cache_limit_behavior()

    except Exception as e:
        print(f"Error during testing: {e}")
        raise


if __name__ == "__main__":
    main()
