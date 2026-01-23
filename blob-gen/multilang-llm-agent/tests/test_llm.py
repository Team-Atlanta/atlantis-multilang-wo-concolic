from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel

from mlla.utils.llm import LLM, accumulate_content


def test_accumulate_content():
    later_content = '''to influence the unmarshalling process in a way that will trigger loading of "jaz.Zer".
    """
    # First byte is the version (must be between 1 and 12)
    # We'll use version 2
    version_byte = bytes([2])

    # For the rest of the payload, we need to craft data that will influence
    # the unmarshalling process to eventually load "jaz.Zer"

    # The OpenWireFormat.unmarshal method will use the data to determine
    # which classes to load. We need to craft this data carefully.

    # Start with a command type that might trigger class loading
    command_type = b"\x01"

    # Include the target class name in a format that might be picked up
    # during class resolution
    class_name = b"jaz.Zer"

    # Add some structure that might influence class loading
    rest_of_payload = command_type + class_name + b"\x00\x00\x00\x00"

    # Combine the version byte with the rest of the payload
    payload = version_byte + rest_of_payload

    return payload
</PAYLOAD_CODE>

Let me reconsider this problem one more time with a fresh perspective.

<PAYLOAD_DESC>
- Target vulnerability: The vulnerability is in the `setVersion()` method where it dynamically loads a class based on the version number using `Class.forName(mfName, false, getClass().getClassLoader())`.

- Key insight: Looking at the code more carefully, I see that the class name is constructed as: `"org.apache.activemq.openwire.v" + version + ".MarshallerFactory"`. The version is validated to be between 1 and 12.

- Exploitation strategy: Since we can't directly control the entire class name, we need to exploit Java's class loading mechanism. In Java, when a class is not found, the classloader may try to load related classes or delegate to other classloaders. If we can make the JVM look for "jaz.Zer" during this process, we can trigger the sanitizer.

- Specific approach: We'll use a version number that will cause the JVM to look for a non-existent class, triggering class loading fallback mechanisms that will eventually try to load "jaz.Zer".

- Expected outcome: When the payload is processed, the JVM will attempt to load "jaz.Zer" during class resolution, triggering the sanitizer.
</PAYLOAD_DESC>

<PAYLOAD_CODE>
def create_payload():
    """
    Create a payload that exploits the reflective class loading vulnerability in ActiveMQ.

    After careful analysis, I've determined that we need to craft a payload that will
    cause the JVM to attempt to load "jaz.Zer" during class resolution.

    Since we can't directly control the entire class name due to version validation,
    we'll use a valid version number and craft the rest of the payload to influence
    the unmarshalling process to load our target class.
    """
    # First byte is the version (must be between 1 and 12)
    # We'll use version 1
    version_byte = bytes([1])

    # The rest of the payload needs to be crafted to influence the unmarshalling process
    # We'll create a payload that includes "jaz.Zer" in a way that might be picked up
    # during class resolution

    # Create a structure that might influence class loading during unmarshalling
    # Include the target class name in various formats and positions
    rest_of_payload = b"jaz.Zer\x00\x00\x00\x01\x00jaz.Zer\x00"

    # Combine the version byte with the rest of the payload
    payload = version_byte + rest_of_payload

    return payload
</PAYLOAD_CODE>

After multiple approaches, I need to completely rethink this problem. Let me analyze the exact vulnerability and how to exploit it.

<PAYLOAD_DESC>
- Target vulnerability: The vulnerability is in the `setVersion()` method where it dynamically loads a class based on the version number using `Class.forName(mfName, false, getClass().getClassLoader())`.

- Key insight: The class name is constructed as: `"org.apache.activemq.openwire.v" + version + ".MarshallerFactory"`. We need to make this class name resolution process eventually load "jaz.Zer".

- Exploitation strategy: After careful analysis, I've realized that we need to exploit Java's class loading delegation and resolution mechanisms. When a class is not found, the JVM will try different paths and classloaders.

- Specific approach: We'll use a version number that will cause the JVM to look for a class that doesn't exist, triggering fallback mechanisms that will eventually try to load "jaz.Zer".

- Expected outcome: When the payload is processed, the JVM will attempt to load "jaz.Zer" during class resolution, triggering the sanitizer.
</PAYLOAD_DESC>

<PAYLOAD_CODE>
def create_payload():
    """
    Create a payload that exploits the reflective class loading vulnerability in ActiveMQ.

    The key insight is that we need to make the JVM load "jaz.Zer" during class resolution.
    Since we can't directly control the entire class name (due to version validation),
    we need to exploit class loading delegation or resolution mechanisms.
    """
    # First byte is the version (must be between 1 and 12)
    # We'll use version 1
    version_byte = bytes([1])

    # For the rest of the payload, we need to craft data that will influence
    # the unmarshalling process to eventually load "jaz.Zer"

    # Create a payload that might trigger class loading of "jaz.Zer"
    # during the unmarshalling process
    rest_of_payload = b"\x00\x01jaz.Zer\x00\x00\x01\x00"

    # Combine the version byte with the rest of the payload
    payload = version_byte + rest_of_payload

    return payload
</PAYLOAD_CODE>'''

    prev_content = '''I'll analyze the code and create a payload to exploit the reflective class loading vulnerability in the ActiveMQ code.

<PAYLOAD_DESC>
- Target vulnerability: The vulnerability is in the `setVersion()` method where it dynamically loads a class based on the version number using `Class.forName(mfName, false, getClass().getClassLoader())`. This is a reflective class loading vulnerability.

- Trigger mechanism: We need to craft a version number that will cause the `mfName` string to be "jaz.Zer" instead of the expected "org.apache.activemq.openwire.v{version}.MarshallerFactory".

- Key conditions satisfied:
  1. Input must be at least 1 byte long (for the version)
  2. Version must be between 1 and 12 (inclusive) to pass the validation check
  3. The version is used to construct the class name for loading

- Data format: The payload consists of a single byte for the version followed by additional bytes that won't be used for exploitation but are needed to satisfy the input processing.

- Expected outcome: When the payload is processed, the `setVersion()` method will attempt to load the class "jaz.Zer" using Class.forName(), triggering the sanitizer.
</PAYLOAD_DESC>

<PAYLOAD_CODE>
def create_payload():
    """
    Create a payload that exploits the reflective class loading vulnerability in ActiveMQ.

    The vulnerability is in the setVersion() method where it constructs a class name
    using the version number and then loads it with Class.forName().

    We need to craft a version number that will make the class name "jaz.Zer" instead
    of the expected "org.apache.activemq.openwire.v{version}.MarshallerFactory".
    """
    # We need to find a version number that will make:
    # "org.apache.activemq.openwire.v" + version + ".MarshallerFactory" == "jaz.Zer"

    # This is impossible with the normal version constraints (1-12),
    # but we can try to exploit string manipulation in Java.

    # The key insight: we need to use a version number that will pass the check
    # but will cause the class name to be "jaz.Zer" when concatenated.

    # Let's use version 10, which is valid (1-12), and then craft the rest of the payload
    # to attempt to manipulate the string construction.

    # First byte is the version (10)
    version_byte = bytes([10])

    # The rest of the payload is arbitrary but needs to be present
    # for the unmarshal operation to have something to work with
    # We'll include "jaz.Zer" in the payload to increase chances of it being used
    # in string operations or error messages
    rest_of_payload = b"jaz.Zer"

    # Combine the version byte with the rest of the payload
    payload = version_byte + rest_of_payload

    return payload
</PAYLOAD_CODE>

Wait, I need to reconsider my approach. The current payload won't work because the class name is directly constructed from the version number. Let me analyze this more carefully.

<PAYLOAD_DESC>
- Target vulnerability: The vulnerability is in the `setVersion()` method where it dynamically loads a class based on the version number using `Class.forName(mfName, false, getClass().getClassLoader())`.

- Exploitation strategy: Looking at how `mfName` is constructed: `"org.apache.activemq.openwire.v" + version + ".MarshallerFactory"`, we need to find a way to make this equal to "jaz.Zer". This is challenging because the version is validated to be between 1 and 12.

- Key insight: In Java, string concatenation with integers converts the integer to a string. If we can manipulate the version number to cause unexpected behavior in this concatenation, we might be able to control the resulting class name.

- Key conditions satisfied:
  1. Input must be at least 1 byte long (for the version)
  2. Version must be between 1 and 12 (inclusive) to pass the validation check
  3. The version is extracted from the first byte of the input

- Data format: The payload consists of a single byte for the version followed by additional bytes that will be used in the unmarshalling process.

- Expected outcome: When the payload is processed, the `setVersion()` method will attempt to load a class name that includes or equals "jaz.Zer", triggering the sanitizer.
</PAYLOAD_DESC>

<PAYLOAD_CODE>
def create_payload():
    """
    Create a payload that exploits the reflective class loading vulnerability in ActiveMQ.

    After careful analysis, I've determined that direct exploitation through the version
    number is not feasible because:
    1. The version is validated to be between 1 and 12
    2. The class name is constructed as "org.apache.activemq.openwire.v" + version + ".MarshallerFactory"

    However, we can try to exploit the unmarshalling process which might lead to loading
    other classes reflectively. The OpenWireFormat.unmarshal method likely uses reflection
    internally when processing commands.

    This payload uses version 7 (valid) and includes the target class name "jaz.Zer"
    in the rest of the payload to increase chances of it being used in reflection operations.
    """
    # First byte is the version (7 is valid, between 1 and 12)
    version_byte = bytes([7])

    # The rest of the payload is crafted to potentially trigger reflection during unmarshalling
    # Include the target class name "jaz.Zer" in various formats
    rest_of_payload = b"jaz.Zer" + b"\0" + b"jaz.Zer" + b"\0" + b"jaz.Zer"

    # Combine the version byte with the rest of the payload
    payload = version_byte + rest_of_payload

    return payload
</PAYLOAD_CODE>

I need to reconsider this problem more carefully. Let me analyze the code flow and vulnerability more precisely.

<PAYLOAD_DESC>
- Target vulnerability: The vulnerability is in the `setVersion()` method where it dynamically loads a class based on the version number using `Class.forName(mfName, false, getClass().getClassLoader())`.

- Key insight: The vulnerability occurs because the version number is used to construct the class name. The class name is constructed as: `"org.apache.activemq.openwire.v" + version + ".MarshallerFactory"`.

- Exploitation strategy: We need to craft a version number that will cause `Class.forName()` to load "jaz.Zer" instead. Since the version is validated to be between 1 and 12, we can't directly control the entire class name. However, Java class loading has a feature where if a class name starts with "org.apache.activemq.openwire.v{version}.", it will look for that package. If we can make the JVM interpret this as looking for a class named "jaz.Zer", we can trigger the sanitizer.

- Key conditions satisfied:
  1. Input must be at least 1 byte long (for the version)
  2. Version must be between 1 and 12 (inclusive)
  3. The marshaller factory class must be constructed to trigger loading "jaz.Zer"

- Data format: The payload consists of a single byte for the version followed by additional bytes that will be used in the unmarshalling process.

- Expected outcome: When the payload is processed, the class loading mechanism will attempt to load "jaz.Zer", triggering the sanitizer.
</PAYLOAD_DESC>

<PAYLOAD_CODE>
def create_payload():
    """
    Create a payload that exploits the reflective class loading vulnerability in ActiveMQ.

    The vulnerability is in the setVersion() method where it constructs a class name
    using the version number and then loads it with Class.forName().

    After careful analysis, I've determined that we can't directly make the class name
    equal to "jaz.Zer" because the version is validated to be between 1 and 12.

    However, we can craft a payload that will cause the unmarshalling process to
    eventually load "jaz.Zer" through other reflective operations that occur during
    command processing.
    """
    # First byte is the version (must be between 1 and 12)
    # Let's use version 7
    version_byte = bytes([7])

    # The rest of the payload needs to be crafted to trigger loading of "jaz.Zer"
    # during the unmarshalling process

    # Create a payload that might trigger class loading during unmarshalling
    # Include the string "jaz.Zer" in various formats and positions
    rest_of_payload = b"\x00\x01jaz.Zer\x00\x00\x01\x00"

    # Combine the version byte with the rest of the payload
    payload = version_byte + rest_of_payload

    return payload
</PAYLOAD_CODE>

I need to take a step back and think more carefully about this problem. Let me analyze the exact class loading mechanism and how we can exploit it.

<PAYLOAD_DESC>
- Target vulnerability: The vulnerability is in the `setVersion()` method where it dynamically loads a class based on the version number using `Class.forName(mfName, false, getClass().getClassLoader())`.

- Key insight: The class name is constructed as: `"org.apache.activemq.openwire.v" + version + ".MarshallerFactory"`. We need to find a way to make this resolve to "jaz.Zer".

- Exploitation strategy: In Java's class loading system, we can use classloader delegation and package/class naming tricks. If we can create a situation where the classloader looks for "jaz.Zer" as part of resolving the requested class, we can trigger the sanitizer.

- Specific approach: We'll use a version number that passes validation (1-12) but will cause the class loading system to eventually try to load "jaz.Zer" through class resolution mechanisms.

- Key conditions satisfied:
  1. Input must be at least 1 byte long (for the version)
  2. Version must be between 1 and 12 (inclusive)
  3. The class loading process must eventually try to load "jaz.Zer"

- Expected outcome: When the payload is processed, the JVM will attempt to load "jaz.Zer" during class resolution, triggering the sanitizer.
</PAYLOAD_DESC>

<PAYLOAD_CODE>
def create_payload():
    """
    Create a payload that exploits the reflective class loading vulnerability in ActiveMQ.

    After analyzing the code, I've determined that we need to craft a special payload
    that will cause the JVM to load "jaz.Zer" during class resolution.

    The key is to use a valid version number (1-12) and then craft the rest of the
    payload to influence the unmarshalling process to load our target class.
    """
    # First byte is the version (must be between 1 and 12)
    # Let's use version 7
    version_byte = bytes([7])

    # The rest of the payload needs to be crafted to influence the unmarshalling process
    # We'll create a payload that includes "jaz.Zer" and has specific structure
    # that might trigger class loading during unmarshalling

    # Create a header that looks like a valid OpenWire command
    header = b"\x01\x00\x00\x00"  # Some command type indicator

    # Include the target class name
    class_name = b"jaz.Zer"

    # Add some padding and structure
    padding = b"\x00\x00\x00\x00\x00\x00\x00\x00"

    # Combine all parts
    rest_of_payload = header + class_name + padding

    # Final payload
    payload = version_byte + rest_of_payload

    return payload
</PAYLOAD_CODE>

I need to take a completely different approach. Let me analyze the exact vulnerability and how to exploit it more precisely.

<PAYLOAD_DESC>
- Target vulnerability: The vulnerability is in the `setVersion()` method where it dynamically loads a class based on the version number using `Class.forName(mfName, false, getClass().getClassLoader())`.

- Key insight: We need to make the class name "jaz.Zer" instead of "org.apache.activemq.openwire.v{version}.MarshallerFactory".

- Exploitation strategy: After careful analysis of the code, I realized that we need to exploit the class loading mechanism in a different way. The version is validated to be between 1 and 12, but we can craft a special version number that, when used in string concatenation, will result in unexpected behavior.

- Specific approach: We'll use a version number that passes the validation check but causes the constructed class name to be "jaz.Zer" through string manipulation or class loading delegation.

- Key conditions satisfied:
  1. Input must be at least 1 byte long (for the version)
  2. Version must be between 1 and 12 (inclusive)
  3. The constructed class name must resolve to "jaz.Zer"

- Expected outcome: When the payload is processed, the JVM will attempt to load "jaz.Zer", triggering the sanitizer.
</PAYLOAD_DESC>

<PAYLOAD_CODE>
def create_payload():
    """
    Create a payload that exploits the reflective class loading vulnerability in ActiveMQ.

    The key insight is that we need to make the JVM load "jaz.Zer" instead of the
    expected marshaller factory class. Since we can't directly control the entire
    class name (due to version validation), we need to exploit class loading
    delegation or string manipulation.

    After careful analysis, I've determined that we need to use a version number
    that will cause the class loading system to eventually try to load "jaz.Zer".
    """
    # First byte is the version (must be between 1 and 12)
    # Let's use version 10
    version_byte = bytes([10])

    # For the rest of the payload, we need to craft data that will influence
    # the unmarshalling process to eventually load "jaz.Zer"

    # Create a payload that includes "jaz.Zer" in a way that might be picked up
    # by the class loading system during unmarshalling
    rest_of_payload = b"\x00\x07jaz.Zer\x00"

    # Combine the version byte with the rest of the payload
    payload = version_byte + rest_of_payload

    return payload
</PAYLOAD_CODE>

After multiple attempts, I need to take a completely different approach. Let me carefully analyze how we can exploit this vulnerability.

<PAYLOAD_DESC>
- Target vulnerability: The vulnerability is in the `setVersion()` method where it dynamically loads a class based on the version number using `Class.forName(mfName, false, getClass().getClassLoader())`.

- Key insight: The class name is constructed as: `"org.apache.activemq.openwire.v" + version + ".MarshallerFactory"`. We need to make this resolve to "jaz.Zer".

- Exploitation strategy: After careful analysis, I've realized we need to exploit Java's classloader delegation mechanism. When a class is not found, the JVM will try different classloaders and paths. We need to craft our input so that during this resolution process, "jaz.Zer" is attempted to be loaded.

- Specific approach: We'll use a version number that will cause the JVM to look for a class that doesn't exist, triggering fallback mechanisms that will eventually try to load "jaz.Zer".

- Key conditions satisfied:
  1. Input must be at least 1 byte long (for the version)
  2. Version must be between 1 and 12 (inclusive)
  3. The class loading process must eventually try to load "jaz.Zer"

- Expected outcome: When the payload is processed, the JVM will attempt to load "jaz.Zer" during class resolution, triggering the sanitizer.
</PAYLOAD_DESC>

<PAYLOAD_CODE>
def create_payload():
    """
    Create a payload that exploits the reflective class loading vulnerability in ActiveMQ.

    After extensive analysis, I've determined that we need to craft a payload that will
    cause the JVM to attempt to load "jaz.Zer" during class resolution.

    The strategy is to use a valid version number and craft the rest of the payload
    to influence the unmarshalling process in a way'''

    content = accumulate_content(later_content, prev_content)
    should_exist_sentence = """    to influence the unmarshalling process in a way that will trigger loading of "jaz.Zer"."""
    assert should_exist_sentence in content


def test_invoke_with_large_model_fallback(update_tokencost, config):
    llm = LLM(
        model="claude-3-7-sonnet-20250219",
        config=config,
    )
    fallback_llm = llm.large_context_model

    short_messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Hello, how are you?"),
    ]
    long_messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Hello, how are you?" * 100000),
    ]

    with patch.object(llm, "_invoke", autospec=True) as mock_invoke, patch.object(
        fallback_llm, "_invoke", autospec=True
    ) as mock_fallback_invoke:

        llm.invoke(short_messages)
        assert mock_invoke.called
        assert not mock_fallback_invoke.called

    context_limit_error = Exception("prompt is too long:")
    with patch.object(
        llm, "_invoke", autospec=True, side_effect=context_limit_error
    ) as mock_invoke, patch.object(
        fallback_llm, "_invoke", autospec=True
    ) as mock_fallback_invoke:

        llm.invoke(long_messages)
        assert mock_invoke.called, "Original model should be called"
        assert mock_fallback_invoke.called, "Fallback model should be called"


@pytest.mark.asyncio
async def test_ainvoke_with_large_model_fallback(update_tokencost, config):
    llm = LLM(
        model="claude-3-7-sonnet-20250219",
        config=config,
    )
    assert llm.large_context_model is not None
    fallback_llm = llm.large_context_model

    short_messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Hello, how are you?"),
    ]
    long_messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Hello, how are you?" * 100000),
    ]

    with patch.object(llm, "_ainvoke", autospec=True) as mock_ainvoke, patch.object(
        fallback_llm, "_ainvoke", autospec=True
    ) as mock_fallback_ainvoke:

        await llm.ainvoke(short_messages)
        assert mock_ainvoke.called
        assert not mock_fallback_ainvoke.called

    context_limit_error = Exception("prompt is too long:")
    with patch.object(
        llm, "_ainvoke", autospec=True, side_effect=context_limit_error
    ) as mock_ainvoke, patch.object(
        fallback_llm, "_ainvoke", autospec=True
    ) as mock_fallback_ainvoke:

        await llm.ainvoke(long_messages)
        assert mock_ainvoke.called
        assert (
            mock_fallback_ainvoke.called
        ), "Fallback should be called after the original model failed"


def test_invoke_with_large_model_fallback_no_large_model(update_tokencost, config):
    llm = LLM(
        model="claude-3-7-sonnet-20250219",
        config=config,
        prepare_large_context_model=False,
    )
    assert llm.large_context_model is None

    messages_short = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Hello, how are you?"),
    ]
    messages_long = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Hello, how are you?" * 100000),
    ]

    with patch.object(llm, "_invoke") as mock_invoke:
        llm.invoke(messages_short)
        assert mock_invoke.called

        mock_invoke.reset_mock()
        llm.invoke(messages_long)
        assert mock_invoke.called


@pytest.mark.asyncio
async def test_ainvoke_with_large_model_fallback_no_large_model(
    update_tokencost, config
):
    llm = LLM(
        model="claude-3-7-sonnet-20250219",
        config=config,
        prepare_large_context_model=False,
    )
    assert llm.large_context_model is None

    short_messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Hello, how are you?"),
    ]
    long_messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Hello, how are you?" * 100000),
    ]

    with patch.object(llm, "_ainvoke", new_callable=AsyncMock) as mock_ainvoke:
        await llm.ainvoke(short_messages)
        assert mock_ainvoke.called

        mock_ainvoke.reset_mock()
        await llm.ainvoke(long_messages)
        assert mock_ainvoke.called


@pytest.mark.parametrize(
    "model, max_tokens, context_limit",
    [
        ("claude-3-7-sonnet-20250219", 64000, 135000),
        ("claude-3-7-sonnet-20250219", 1, 198999),
        ("claude-3-7-sonnet-20250219", 0, 135000),  # 0 output is not allowed
        ("claude-3-7-sonnet-20250219", None, 135000),  # None output is not allowed
        ("gemini-2.5-pro", 64000, 983576),
        ("gemini-2.5-pro", 1, 1047575),
    ],
)
def test_get_context_limit(update_tokencost, config, model, max_tokens, context_limit):
    llm = LLM(
        model=model,
        config=config,
        max_tokens=max_tokens,
    )
    assert llm.get_context_limit() == context_limit


@pytest.mark.parametrize("max_tokens", [128000, 64000])
def test_large_context_token_cost(request, update_tokencost, config, max_tokens):
    if request.config.getoption("--ci"):
        pytest.skip("Skipping test in CI mode because it uses LLM")
        return

    llm = LLM(
        model="claude-3-7-sonnet-20250219",
        config=config,
        max_tokens=max_tokens,
        prepare_large_context_model=True,
    )
    assert llm.large_context_model is not None

    # To test max_token difference between an original and a large context model
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Give me numbers from 1 to 10"),
    ]
    with patch.object(llm, "get_context_limit", return_value=1):
        result = llm.invoke(messages)
        logger.info(result)


@pytest.mark.parametrize(
    "model_name",
    [
        "claude-sonnet-4-20250514",
        "o4-mini",
        "gpt-4o-mini",
    ],
)
def test_large_context_model_output_format(update_tokencost, config, model_name):

    class TestOutput(BaseModel):
        values: list[str]

    llm = LLM(
        model=model_name,
        config=config,
        prepare_large_context_model=True,
        output_format=TestOutput,
        max_tokens=1000,
    )

    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Give me numbers from 1 to 10"),
    ]
    # with patch.object(llm, "get_context_limit", return_value=100):

    context_limit_error = Exception("prompt is too long:")
    with patch.object(llm, "_invoke", side_effect=context_limit_error):
        original_response = llm.invoke(messages)
        original_result = original_response[-1]
        assert isinstance(original_result, TestOutput)
