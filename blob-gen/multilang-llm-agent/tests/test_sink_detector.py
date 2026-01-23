from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import BaseMessage

from mlla.utils.cg import FuncInfo, LocationInfo
from mlla.utils.cg.visitor import SinkDetectReport, SinkDetectVisitor
from mlla.utils.context import GlobalContext


@pytest.fixture
def sink_detect_visitor(config: GlobalContext, random_project_name):
    config.cp.name = random_project_name
    visitor = SinkDetectVisitor(config)
    return visitor


@pytest.mark.asyncio
async def test_bcda_sink_detect_cache(
    sink_detect_visitor: SinkDetectVisitor, monkeypatch
):
    sink_detect_report = SinkDetectReport(
        sink_analysis_message="mocked_message",
        is_vulnerable=True,
        sink_line="mocked_sink_line",
        sink_line_number=1,
        sanitizer_candidates=["mocked_sanitizer_candidate"],
    )
    mock_analyze_for_sinks = AsyncMock(return_value=sink_detect_report)

    monkeypatch.setattr(
        sink_detect_visitor, "_analyze_for_sinks", mock_analyze_for_sinks
    )

    dummy_func_info = FuncInfo(
        func_location=LocationInfo(
            func_name="target_1",
            file_path="mock.c",
            start_line=1,
            end_line=10,
        ),
        func_body="body\n\n\n\n\n\n\n\n\n",
    )
    # cache miss
    assert dummy_func_info.sink_detector_report is None
    await sink_detect_visitor.async_visit(dummy_func_info)
    assert mock_analyze_for_sinks.call_count == 1
    assert dummy_func_info.sink_detector_report is not None

    # cache hit
    await sink_detect_visitor.async_visit(dummy_func_info)
    assert mock_analyze_for_sinks.call_count == 1

    # cache hit
    await sink_detect_visitor.async_visit(dummy_func_info)
    assert mock_analyze_for_sinks.call_count == 1


@pytest.mark.asyncio
async def test_bcda_sink_detect_cache_key(sink_detect_visitor, monkeypatch):
    sink_detect_visitor.sink_detect_cache.get = Mock(return_value=None)
    mock_analyze = AsyncMock(
        return_value=SinkDetectReport(
            sink_analysis_message="dummy_message",
            is_vulnerable=False,
            sink_line="dummy_sink_line",
            sink_line_number=1,
            sanitizer_candidates=["dummy_sanitizer_candidate"],
        )
    )
    monkeypatch.setattr(sink_detect_visitor, "_analyze_for_sinks", mock_analyze)
    spy_set = Mock()
    sink_detect_visitor.sink_detect_cache.set = spy_set

    node1 = FuncInfo(
        func_location=LocationInfo(
            func_name="A", file_path="x.c", start_line=1, end_line=2
        ),
        func_body="b",
    )
    node2 = FuncInfo(
        func_location=LocationInfo(
            func_name="B", file_path="y.c", start_line=3, end_line=4
        ),
        func_body="c",
    )

    assert node1.sink_detector_report is None
    assert node2.sink_detector_report is None
    await sink_detect_visitor.async_visit(node1)
    await sink_detect_visitor.async_visit(node2)
    assert node1.sink_detector_report is not None
    assert node2.sink_detector_report is not None

    assert mock_analyze.await_count == 2
    assert spy_set.call_count == 2
    keys = {args[0][0] for args in spy_set.call_args_list}
    assert keys == {
        f"bcda_sd::{node1.create_tag(verbose=False)}",
        f"bcda_sd::{node2.create_tag(verbose=False)}",
    }


@pytest.mark.asyncio
async def test_bcda_sink_detect_cache_empty_body(sink_detect_visitor, monkeypatch):
    sink_detect_visitor.sink_detect_cache.get = Mock()
    mock_analyze = AsyncMock()
    monkeypatch.setattr(sink_detect_visitor, "_analyze_for_sinks", mock_analyze)

    dummy = FuncInfo(
        func_location=LocationInfo(
            func_name="f", file_path="file.c", start_line=1, end_line=2
        ),
        func_body="",
    )
    assert dummy.sink_detector_report is None
    await sink_detect_visitor.async_visit(dummy)
    assert dummy.sink_detector_report is None, "should not be cached"

    # nothing should be called with empty body
    mock_analyze.assert_not_called()
    sink_detect_visitor.sink_detect_cache.get.assert_not_called()


@pytest.mark.asyncio
async def test_bcda_sink_detect_cache_invalid_json(sink_detect_visitor, monkeypatch):
    sink_detect_visitor.sink_detect_cache.get = Mock(return_value="not a json")

    expected_report = SinkDetectReport(
        sink_analysis_message="dummy_message",
        is_vulnerable=False,
        sink_line="dummy_sink_line",
        sink_line_number=1,
        sanitizer_candidates=["dummy_sanitizer_candidate"],
    )
    mock_analyze = AsyncMock(return_value=expected_report)
    monkeypatch.setattr(sink_detect_visitor, "_analyze_for_sinks", mock_analyze)
    sink_detect_visitor.sink_detect_cache.set = Mock()

    dummy = FuncInfo(
        func_location=LocationInfo(
            func_name="tag", file_path="f.c", start_line=1, end_line=1
        ),
        func_body="body",
    )
    assert dummy.sink_detector_report is None
    await sink_detect_visitor.async_visit(dummy)
    assert dummy.sink_detector_report is not None

    # Should be called once when invalid data is cached
    mock_analyze.assert_awaited_once()
    cache_key = f"bcda_sd::{dummy.create_tag(verbose=False)}"
    sink_detect_visitor.sink_detect_cache.set.assert_called_once_with(
        cache_key, expected_report.model_dump_json()
    )


@pytest.mark.asyncio
async def test_bcda_sink_detect_cache_analysis_failed(sink_detect_visitor, monkeypatch):
    sink_detect_visitor.sink_detect_cache.get = Mock(return_value=None)
    mock_analyze = AsyncMock(return_value=None)
    monkeypatch.setattr(sink_detect_visitor, "_analyze_for_sinks", mock_analyze)
    sink_detect_visitor.sink_detect_cache.set = Mock()

    dummy = FuncInfo(
        func_location=LocationInfo(
            func_name="t", file_path="f.c", start_line=1, end_line=2
        ),
        func_body="x",
    )
    await sink_detect_visitor.async_visit(dummy)

    mock_analyze.assert_awaited_once()
    sink_detect_visitor.sink_detect_cache.set.assert_not_called()
    assert dummy.sink_detector_report is None


@pytest.mark.skip(reason="This test uses real LLM.")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous_result, answer",
    [
        (
            SinkDetectReport(
                sink_analysis_message=(
                    "The function 'testUserRemoteConfig' receives data from a"
                    " ByteBuffer, splits it into parts, and then uses parts[0] directly"
                    " to retrieve a method via reflection without any validation. Since"
                    " parts[0] is attacker controlled, this could allow an attacker to"
                    " invoke arbitrary methods within the UserRemoteConfig class,"
                    " leading to a reflective call vulnerability."
                ),
                is_vulnerable=True,
                sink_line=(
                    "Method method = UserRemoteConfig.class.getMethod(parts[0],"
                    " String.class);"
                ),
                sink_line_number=9,
                sanitizer_candidates=["ReflectiveCall.class"],
            ),
            ["ReflectiveCall"],
        ),
        (
            SinkDetectReport(
                sink_analysis_message=(
                    "The function retrieves the 'ip' parameter from the request and"
                    " later uses it directly inside a Pattern.compile call without"
                    " proper escaping. Although there is a check using isValidIPFormat,"
                    " it can be bypassed if the 'skip' parameter is provided (or by an"
                    " admin), allowing potentially malicious input to be concatenated"
                    " into the regex. This introduces a risk of regex injection, which"
                    " can be exploited by crafted inputs that break the intended regex"
                    " structure."
                ),
                is_vulnerable=True,
                sink_line=(
                    'Pattern pattern = Pattern.compile(\\".*(\\" + ip + \\").*\\");'
                ),
                sink_line_number=18,
                sanitizer_candidates=["RegexInjection.pattern_syntax"],
            ),
            ["RegexInjection"],
        ),
        (
            SinkDetectReport(
                sink_analysis_message=(
                    "The function concatenates unsanitized user inputs 'username' and"
                    " 'key' into an LDAP search filter on line 23. This creates an LDAP"
                    " injection vulnerability (specifically LDAP search filter"
                    " injection) since attacker-controlled data is directly used in"
                    " constructing the filter without proper sanitization."
                ),
                is_vulnerable=True,
                sink_line=(
                    'String searchFilter = \\"(&(objectClass=inetOrgPerson)(cn=\\" +'
                    ' username + \\")(userPassword=\\" + key + \\"))\\";'
                ),
                sink_line_number=23,
                sanitizer_candidates=["LdapInjection.filter_chars"],
            ),
            ["LdapInjection"],
        ),
        # (
        #     SinkDetectReport(
        #         analysis_message=(
        #             "The function testApi accepts a ByteBuffer
        # that is converted to a String and split into three parts.
        # The third part (parts[2]) comes entirely from
        # attacker-controlled data without any validation or
        # sanitization. This value is then passed directly as an
        # argument to Api.doXml. Since no checks are performed on
        # parts[2] before it reaches this sensitive sink, an
        # attacker could craft malicious input that might be
        # exploited within the doXml method. Although the exact
        # internals of doXml aren’t shown, this direct data flow
        # from untrusted input to a sensitive API call represents a
        # potential vulnerability. None of the provided sanitizer
        # candidates directly match this code pattern, so we label
        # it as N/A."
        #         ),
        #         is_vulnerable=True,
        #         sink_line="new Api(jenkins).doXml(req, rsp, parts
        # [2], null, null, 0);",
        #         sink_line_number=11,
        #         sanitizer_candidates=["N/A"],
        #     ),
        #     [],
        # ),
    ],
)
async def test_analyze_for_sinks_invalid_sanitizer_type(
    sink_detect_visitor, monkeypatch, previous_result, answer
):
    return_value = [BaseMessage(content=previous_result.model_dump_json(), type="text")]

    mock_llm = AsyncMock(return_value=return_value)
    monkeypatch.setattr(sink_detect_visitor.llm, "ainvoke", mock_llm)

    dummy_func_info = FuncInfo(
        func_location=LocationInfo(
            func_name="dummy_func",
            file_path="dummy.c",
            start_line=1,
            end_line=1,
        ),
        func_body="dummy_func_body",
    )

    result = await sink_detect_visitor._analyze_for_sinks(dummy_func_info)
    assert result is not None
    assert result.is_vulnerable == previous_result.is_vulnerable
    assert result.sink_line == previous_result.sink_line
    assert result.sink_line_number == previous_result.sink_line_number
    assert result.sanitizer_candidates == answer
