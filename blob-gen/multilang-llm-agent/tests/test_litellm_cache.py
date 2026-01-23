import os
import random
import urllib.error
import urllib.request

import pytest
from langchain_core.messages import HumanMessage

from mlla.utils.llm import LLM
from mlla.utils.telemetry import setup_telemetry
from tests.dummy_context import DummyContext

# Disable all logging
# import logging
# from loguru import logger
# logger.remove()
# logging.getLogger("langchain").setLevel(logging.ERROR)
# logging.getLogger("LiteLLM").setLevel(logging.ERROR)

pytestmark = pytest.mark.skip(reason="This test is not for mlla.")


def get_random_array():
    """Generate a random array for examples"""
    size = random.randint(5, 10)
    return [random.randint(1, 100) for _ in range(size)]


def test_litellm_cache_status():
    """Test if litellm proxy cache is working"""
    litellm_url = os.getenv("LITELLM_URL")
    litellm_key = os.getenv("LITELLM_KEY")

    if not litellm_url or not litellm_key:
        raise ValueError("LITELLM_URL and LITELLM_KEY must be set")

    request = urllib.request.Request(
        f"{litellm_url}/cache/ping", headers={"Authorization": f"Bearer {litellm_key}"}
    )
    try:
        with urllib.request.urlopen(request) as response:
            assert response.status == 200
    except urllib.error.URLError as e:
        raise ValueError(f"Failed to connect to cache: {e}")


def test_cache_with_direct_model():
    """Test cache with direct model (gpt4o) by making same request twice"""
    config = DummyContext(no_llm=False)
    config.is_dev = False  # Disable logs
    llm = LLM(model="gpt-4o", config=config)

    # Generate random array for the example
    arr = get_random_array()

    # First request with a long prompt including random elements
    long_prompt = f"""Please provide a detailed analysis of the following \
    sorting algorithm:

    def bubble_sort(arr):
        n = len(arr)
        for i in range(n):
            for j in range(0, n - i - 1):
                if arr[j] > arr[j + 1]:
                    arr[j], arr[j + 1] = arr[j + 1], arr[j]
        return arr

    For example, if we run this algorithm on the array {arr},
    what would be the step-by-step process? What is the time complexity?
    How does it compare to other sorting algorithms like quicksort or mergesort?
    What are its advantages and disadvantages?"""

    # Make two requests with same parameters
    messages = [HumanMessage(content=long_prompt)]
    response1 = llm.invoke(messages)

    messages = [HumanMessage(content=long_prompt)]
    response2 = llm.invoke(messages)

    # If cache is working, responses should be identical
    assert (
        response1[-1].content == response2[-1].content
    ), "Cache not working - responses differ for identical requests"


def test_cache_with_temperature():
    """Test cache behavior with different temperature settings"""
    config = DummyContext(no_llm=False)
    config.is_dev = False  # Disable logs

    # Generate random numbers for the example
    target = random.randint(1, 100)
    arr = sorted([random.randint(1, 100) for _ in range(10)])

    # Use a long prompt with random elements
    long_prompt = f"""Please analyze this binary search implementation and \
    discuss its efficiency:

    def binary_search(arr, target):
        left, right = 0, len(arr) - 1
        while left <= right:
            mid = (left + right) // 2
            if arr[mid] == target:
                return mid
            elif arr[mid] < target:
                left = mid + 1
            else:
                right = mid - 1
        return -1

    For example, if we search for {target} in the array {arr},
    what would be the step-by-step process? What is the time complexity?
    How does it compare to linear search? What are the requirements for using
    binary search? Please explain how it would find (or fail to find) the target."""

    # Test with zero temperature (should use cache)
    llm1 = LLM(model="gpt-4o", config=config, temperature=0)
    messages = [HumanMessage(content=long_prompt)]
    response1 = llm1.invoke(messages)

    messages = [HumanMessage(content=long_prompt)]
    response2 = llm1.invoke(messages)
    assert (
        response1[-1].content == response2[-1].content
    ), "Cache not working - responses differ for same temperature"

    # Test with non-zero temperature (should not use cache)
    llm2 = LLM(model="gpt-4o", config=config, temperature=0.6)
    messages = [HumanMessage(content=long_prompt)]
    response3 = llm2.invoke(messages)
    assert (
        response1[-1].content != response3[-1].content
    ), "Cache incorrectly used - responses same for different temperatures"

    messages = [HumanMessage(content=long_prompt)]
    response4 = llm2.invoke(messages)
    assert (
        response3[-1].content != response4[-1].content
    ), "Cache incorrectly used - responses same for non-zero temperatures"


def setup():
    """Setup function to initialize environment"""
    from dotenv import load_dotenv

    load_dotenv(".env.secret")

    project_name = "test_caching3"
    endpoint = "http://localhost:6006/v1/traces"

    setup_telemetry(
        project_name=project_name,
        endpoint=endpoint,
    )


if __name__ == "__main__":
    setup()  # Load environment variables

    # Test cache status
    print("Testing cache status...")
    test_litellm_cache_status()
    print("✓ Cache status test passed")

    # Test cache with direct model
    print("Testing cache with direct model...")
    test_cache_with_direct_model()
    print("✓ Cache test with direct model passed")

    # Test cache with temperature
    print("Testing cache with different temperatures...")
    test_cache_with_temperature()
    print("✓ Cache test with temperature passed")

    print("\nAll tests passed successfully!")
