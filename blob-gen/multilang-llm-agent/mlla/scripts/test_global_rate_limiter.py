import asyncio
import getpass
import os
import time

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_openai import ChatOpenAI

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

# Global rate limiter instance (shared across all instances)
GLOBAL_RATE_LIMITER = InMemoryRateLimiter(
    requests_per_second=2,  # Low limit for easy testing
    check_every_n_seconds=0.1,
    max_bucket_size=2,
)


class TestLLMClassPerInstance:
    """Test class with per-instance rate limiter (current behavior)"""

    def __init__(self, name):
        self.name = name
        # Each instance creates its own rate limiter
        self.rate_limiter = InMemoryRateLimiter(
            requests_per_second=2,  # Low limit for easy testing
            check_every_n_seconds=0.1,
            max_bucket_size=2,
        )
        self.chat_model = ChatOpenAI(
            model="gpt-4.1-nano",
            api_key=KEY,
            base_url=URL,
            rate_limiter=self.rate_limiter,
        )

    async def make_request(self):
        try:
            print(f"{self.name}: Request started at {time.time():.3f}")
            await self.chat_model.ainvoke([HumanMessage(content="test")])
            print(f"{self.name}: Request completed at {time.time():.3f}")
            return f"{self.name} response"
        except Exception:
            print(
                f"{self.name}: Request completed at {time.time():.3f} (API call made)"
            )
            return f"{self.name} completed"


class TestLLMClassGlobal:
    """Test class with global rate limiter (proposed behavior)"""

    def __init__(self, name):
        self.name = name
        # All instances share the same global rate limiter
        self.chat_model = ChatOpenAI(
            model="gpt-4.1-nano",
            api_key=KEY,
            base_url=URL,
            rate_limiter=GLOBAL_RATE_LIMITER,  # Shared global instance
        )

    async def make_request(self):
        try:
            print(f"{self.name}: Request started at {time.time():.3f}")
            await self.chat_model.ainvoke([HumanMessage(content="test")])
            print(f"{self.name}: Request completed at {time.time():.3f}")
            return f"{self.name} response"
        except Exception:
            print(
                f"{self.name}: Request completed at {time.time():.3f} (API call made)"
            )
            return f"{self.name} completed"


async def test_per_instance_rate_limiter():
    """Test the current per-instance rate limiter behavior"""
    print("=" * 60)
    print("TESTING PER-INSTANCE RATE LIMITER (Current Behavior)")
    print("=" * 60)

    # Create two instances with separate rate limiters
    llm1 = TestLLMClassPerInstance("LLM1")
    llm2 = TestLLMClassPerInstance("LLM2")

    print("Each instance has its own rate_limiter with 2 requests/second")

    # Test: Simultaneous requests from both instances
    print("\n=== Simultaneous requests from both instances ===")
    start_time = time.time()
    tasks = []
    # 5 requests from each instance = 10 total requests
    for i in range(5):
        tasks.append(llm1.make_request())
        tasks.append(llm2.make_request())
    await asyncio.gather(*tasks)
    per_instance_time = time.time() - start_time
    print(f"Per-instance test took: {per_instance_time:.2f} seconds")

    return per_instance_time


async def test_global_rate_limiter():
    """Test the proposed global rate limiter behavior"""
    print("\n" + "=" * 60)
    print("TESTING GLOBAL RATE LIMITER (Proposed Behavior)")
    print("=" * 60)

    # Create two instances sharing the same global rate limiter
    llm1 = TestLLMClassGlobal("LLM1")
    llm2 = TestLLMClassGlobal("LLM2")

    print("Both instances share the same global rate_limiter with 2 requests/second")

    # Test: Simultaneous requests from both instances
    print("\n=== Simultaneous requests from both instances ===")
    start_time = time.time()
    tasks = []
    # 5 requests from each instance = 10 total requests
    for i in range(5):
        tasks.append(llm1.make_request())
        tasks.append(llm2.make_request())
    await asyncio.gather(*tasks)
    global_time = time.time() - start_time
    print(f"Global rate limiter test took: {global_time:.2f} seconds")

    return global_time


async def main():
    print("Testing rate limiter behavior comparison...")
    print("Each test makes 10 simultaneous requests (5 from each instance)")

    # Test per-instance behavior
    per_instance_time = await test_per_instance_rate_limiter()

    # Wait between tests
    await asyncio.sleep(2)

    # Test global behavior
    global_time = await test_global_rate_limiter()

    # Analysis
    print("\n" + "=" * 60)
    print("ANALYSIS")
    print("=" * 60)
    print(f"Per-instance rate limiter time: {per_instance_time:.2f} seconds")
    print(f"Global rate limiter time:       {global_time:.2f} seconds")
    print(
        f"Difference:                     {global_time - per_instance_time:.2f} seconds"
    )

    # Calculate the expected theoretical times
    theoretical_per_instance = (
        10 / 4
    )  # 10 requests / 4 req/sec (2 instances × 2 req/sec each)
    theoretical_global = 10 / 2  # 10 requests / 2 req/sec (shared limit)

    print("\nTheoretical times:")
    print(f"Per-instance (4 req/sec total): {theoretical_per_instance:.2f} seconds")
    print(f"Global (2 req/sec total):       {theoretical_global:.2f} seconds")

    # Check if global is significantly slower (at least 25% slower)
    if global_time > per_instance_time * 1.25:
        print("\n✅ GLOBAL RATE LIMITER IS WORKING!")
        print(
            "   - Per-instance: Each instance can make 2 req/sec independently (4"
            " req/sec total)"
        )
        print("   - Global: All instances share the same 2 req/sec limit")
        print("   - Global rate limiting properly coordinates across all instances")
        print(
            f"   - Global is {((global_time / per_instance_time - 1) * 100):.1f}%"
            " slower, confirming coordination"
        )
    else:
        print("\n❌ RATE LIMITERS MIGHT NOT BE WORKING AS EXPECTED")
        print("   - Times are too similar, check the implementation")


if __name__ == "__main__":
    asyncio.run(main())
