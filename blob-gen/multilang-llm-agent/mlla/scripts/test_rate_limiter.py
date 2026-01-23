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


class TestLLMClass:
    def __init__(self, name):
        self.name = name
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
            # This will fail due to dummy API key, but rate limiting should still work
            await self.chat_model.ainvoke([HumanMessage(content="test")])
            print(f"{self.name}: Request completed at {time.time():.3f}")
            return f"{self.name} response"
        except Exception:
            print(
                f"{self.name}: Request failed at {time.time():.3f} (expected due to"
                " dummy API key)"
            )
            return f"{self.name} failed"


async def test_rate_limiter_behavior():
    # Create two instances
    llm1 = TestLLMClass("LLM1")
    llm2 = TestLLMClass("LLM2")

    print("Testing rate limiter behavior with multiple instances...")
    print("Each instance has rate_limiter with 2 requests/second")

    # Test 1: Make requests from same instance
    print("\n=== Test 1: Multiple requests from same instance ===")
    start_time = time.time()
    tasks1 = [llm1.make_request() for _ in range(4)]
    await asyncio.gather(*tasks1)
    print(f"Same instance test took: {time.time() - start_time:.2f} seconds")

    # Wait a bit between tests
    await asyncio.sleep(1)

    # Test 2: Make requests from different instances
    print("\n=== Test 2: Requests from different instances ===")
    start_time = time.time()
    tasks2 = []
    for i in range(4):
        if i % 2 == 0:
            tasks2.append(llm1.make_request())
        else:
            tasks2.append(llm2.make_request())
    await asyncio.gather(*tasks2)
    print(f"Different instances test took: {time.time() - start_time:.2f} seconds")

    # Wait a bit between tests
    await asyncio.sleep(1)

    # Test 3: Simultaneous requests from both instances
    print("\n=== Test 3: Simultaneous requests from both instances ===")
    start_time = time.time()
    tasks3 = [
        llm1.make_request(),
        llm1.make_request(),
        llm2.make_request(),
        llm2.make_request(),
    ]
    await asyncio.gather(*tasks3)
    print(f"Simultaneous test took: {time.time() - start_time:.2f} seconds")

    print("\n=== Analysis ===")
    print("If rate limiting is PER-INSTANCE:")
    print("  - Test 1 should be slowest (4 requests through 1 rate limiter)")
    print("  - Test 2 & 3 should be faster (requests split across 2 rate limiters)")
    print("\nIf rate limiting is GLOBAL:")
    print("  - All tests should take similar time (all requests coordinated)")


if __name__ == "__main__":
    asyncio.run(test_rate_limiter_behavior())
