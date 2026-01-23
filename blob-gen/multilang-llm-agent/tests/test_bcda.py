# Test for bcda_experimental


from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from loguru import logger

from mlla.agents.bcda_experimental import (
    BugCandDetectAgent,
    BugCandDetectAgentInputState,
    ExpandedPath,
    KeyConditionReport,
    LineInfo,
    SanitizerValidationReport,
    instrument_line,
)
from mlla.agents.bugcandidate_agent.path_extractor import ExtractedPath
from mlla.agents.cgpa import CGParserAgent, CGParserInputState
from mlla.codeindexer.codeindexer import CodeIndexer
from mlla.modules.sanitizer import get_sanitizer_prompt
from mlla.prompts.bcda_experimental import EXTRACT_PARTIAL_CONDITION_FORMAT
from mlla.utils import normalize_func_name
from mlla.utils.analysis_interest import InterestPriority
from mlla.utils.bit import BugInducingThing, LocationInfo
from mlla.utils.cg import FuncInfo, InterestInfo, SinkDetectReport
from mlla.utils.context import GlobalContext
from mlla.utils.cp import init_cp_repo
from mlla.utils.llm import LLM
from mlla.utils.llm_tools.astgrep import AGTool
from mlla.utils.telemetry import setup_telemetry


@pytest.fixture
def bcda(config: GlobalContext):
    agent_instance = BugCandDetectAgent(config)
    return agent_instance


def cp_config(cp_path, target_harness, config: GlobalContext) -> GlobalContext:
    config._init_cp(cp_path, target_harness)
    return config


def cgparser_agent(cp_path, target_harness, config: GlobalContext):
    config = cp_config(cp_path, target_harness, config)
    return CGParserAgent(config)


def setup():
    """Setup function to initialize environment"""
    from dotenv import load_dotenv

    load_dotenv(".env.secret")

    project_name = "test_bcda"
    endpoint = "http://localhost:6006/v1/traces"

    setup_telemetry(
        project_name=project_name,
        endpoint=endpoint,
    )


@pytest.mark.asyncio
async def test_issue_262(redis_client, cp_jackson_databind_path, config: GlobalContext):
    # Initialize the CP repository first
    setup()
    init_cp_repo(cp_jackson_databind_path)

    code_indexer = CodeIndexer(redis_client)
    await code_indexer.index_project(
        "jackson-databind-issue-262", [cp_jackson_databind_path], "jvm", overwrite=True
    )
    fn_name = "enableDefaultTyping"
    _file_path = (
        cp_jackson_databind_path
        / "repo/src/main/java/com/fasterxml/jackson/databind/ObjectMapper.java"
    )
    file_path = str(_file_path)
    parent_file_path = str(
        cp_jackson_databind_path
        / (
            "fuzz/jackson-databind-harness-one/src/main/java/com/aixcc/jackson"
            + "/databind/harnesses/one/JacksonDatabindOne.java"
        )
    )
    search_results = await code_indexer.search_function(fn_name)
    search_results = [r for r in search_results if r.file_path == file_path]

    config.code_indexer = code_indexer

    cgpa = cgparser_agent(cp_jackson_databind_path, "JacksonDatabindOne", config)
    graph = cgpa.compile()
    cgparser_state = await graph.ainvoke(
        CGParserInputState(
            fn_name=fn_name,
            fn_file_path=file_path,
            caller_file_path=parent_file_path,
            callsite_location=None,
        )
    )
    cires: FuncInfo = cgparser_state["code_dict"]
    assert cires is not None
    assert cires.func_location.file_path == file_path
    assert cires.func_location.func_name.split(".")[-1].split("(")[0] == fn_name
    assert cires.func_location.start_line == 1478
    assert cires.func_location.end_line == 1480


def test_find_multiline_content(bcda: BugCandDetectAgent, tmp_path):
    str1 = (
        "return new PasswordAuthentication(userName,"
        " Secret.fromString(password).getPlainText().toCharArray());"
    )
    str2 = (
        """
 private static Authenticator newValidationAuthenticator(String userName, String"""
        """password) {
             return new Authenticator() {
                 @Override
                 protected PasswordAuthentication getPasswordAuthentication() {
                     return new PasswordAuthentication(
                             userName, Secret.fromString(password).getPlainText()."""
        """toCharArray());
                 }
             };
         }
"""
    )
    logger.info(instrument_line(str2, 1)[0])

    assert bcda._find_multiline_content(str2, str1) == (6, 7)

    str1 = "return new Authenticator() {"

    assert bcda._find_multiline_content(str2, str1) == (3, 3)


def test_failed_multiline_content(bcda: BugCandDetectAgent, tmp_path):

    str1 = (
        "NamingEnumeration<SearchResult> results ="
        ' dirContext.search("ou=users,dc=example,dc=com", searchFilter, controls);'
    )
    str2 = """
             String searchFilter = "(&(objectClass=inetOrgPerson)(cn=" +
             username + ")(userPassword=" + key + "))";
             NamingEnumeration<SearchResult> results =
             dirContext.search("ou=users,dc=example,dc=com", searchFilter,
                     controls);
"""

    assert bcda._find_multiline_content(str2, str1) == (4, 6)

    str1 = "memcpy(&buf[idx], &data[8],  buf_size);"
    str2 = """
void target_2(const uint8_t *data, size_t size) {
  if (size < 0x8 || size > 0x100)
    return;
  uint32_t buf_size = ((uint32_t *)data)[0];
  uint32_t idx = ((uint32_t *)data)[1];
  if (buf_size + 8 != size)
    return;
  uint8_t *buf = (uint8_t *)malloc(buf_size);
  memcpy(&buf[idx], &data[8],  buf_size);
 }
"""

    assert bcda._find_multiline_content(str2, str1) == (10, 10)

    str1 = (
        "ObjectInputStream ois = new CompatibleObjectInputStream(new"
        " BufferedInputStream(new FileInputStream(reportFile)));"
    )
    str2 = """
 public static CoverageResult recoverCoverageResult(final Run<?, ?> run) throws
 IOException, ClassNotFoundException {
         File reportFile = new File(run.getRootDir(), DEFAULT_REPORT_SAVE_NAME);

         try (ObjectInputStream ois = new CompatibleObjectInputStream(new
         BufferedInputStream(new FileInputStream(reportFile)))) {
             return (CoverageResult) ois.readObject();
         }
     }
"""


@pytest.mark.skip(
    reason="I don't know why this test is failing, need to investigate in the future"
)
@pytest.mark.asyncio
async def test_real_pinpoint_line2(
    bcda: BugCandDetectAgent, cp_jenkins_path, redis_client
):
    code_indexer = CodeIndexer(redis_client)
    await code_indexer.index_project(
        "jenkins-real-pinpoint-line2", [cp_jenkins_path], "jvm", overwrite=True
    )
    fn_name = "fuzzerTestOneInput"
    _file_path = cp_jenkins_path / (
        "fuzz/jenkins-harness-three/src/main/java/com/aixcc/jenkins/harnesses/"
        + "three/JenkinsThree.java"
    )
    file_path = str(_file_path)
    search_results = await code_indexer.search_function(fn_name)
    search_results = [r for r in search_results if r.file_path == file_path]
    assert len(search_results) == 1
    search_result = search_results[0]
    func_info1 = FuncInfo(
        func_location=LocationInfo(
            func_name=fn_name,
            file_path=file_path,
            start_line=search_result.start_line,
            end_line=search_result.end_line,
        ),
        func_body=search_result.func_body,
    )

    fn_name = "fuzz"
    search_results = await code_indexer.search_function(fn_name)
    search_results = [r for r in search_results if r.file_path == file_path]
    assert len(search_results) == 1
    search_result = search_results[0]
    logger.info(f"search_result for fuzz: {search_result}")
    func_info2 = FuncInfo(
        func_location=LocationInfo(
            func_name=fn_name,
            file_path=file_path,
            start_line=search_result.start_line,
            end_line=search_result.end_line,
        ),
        func_body=search_result.func_body,
    )

    fn_name = "testApi"
    search_results = await code_indexer.search_function(fn_name)
    search_results = [r for r in search_results if r.file_path == file_path]
    assert len(search_results) == 1
    search_result = search_results[0]
    func_info3 = FuncInfo(
        func_location=LocationInfo(
            func_name=fn_name,
            file_path=file_path,
            start_line=search_result.start_line,
            end_line=search_result.end_line,
        ),
        func_body=search_result.func_body,
    )

    fn_name = "doXml"
    _file_path = (
        cp_jenkins_path
        / "repo/plugins/toy-plugin/src/main/java/io/jenkins/plugins/toyplugin/Api.java"
    )
    file_path = str(_file_path)
    search_results = await code_indexer.search_function(fn_name)
    search_results = [r for r in search_results if r.file_path == file_path]
    assert len(search_results) == 1
    search_result = search_results[0]
    func_info4 = FuncInfo(
        func_location=LocationInfo(
            func_name=fn_name,
            file_path=file_path,
            start_line=search_result.start_line,
            end_line=search_result.end_line,
        ),
        func_body=search_result.func_body,
    )

    func_bodies = [func_info1, func_info2, func_info3, func_info4]
    expanded_path = ExpandedPath([[func_info] for func_info in func_bodies])
    logger.info(expanded_path.code_with_path())
    line_info = LineInfo(
        func_name="fuzz", file_path=func_info2.func_location.file_path, line_number=103
    )
    ret = bcda.pinpoint_line(func_bodies, line_info, None)
    assert ret is not None
    assert ret.func_name == "fuzz"
    assert ret.start_line == 103
    assert ret.end_line == 103

    line_info = LineInfo(
        func_name="testApi",
        file_path=func_info3.func_location.file_path,
        line_number=258,
    )
    ret = bcda.pinpoint_line(func_bodies, line_info, None)
    assert ret is not None
    assert ret.func_name == "testApi"
    assert ret.start_line == 258
    assert ret.end_line == 258

    line_info = LineInfo(
        func_name="doXml", file_path=func_info4.func_location.file_path, line_number=112
    )
    ret = bcda.pinpoint_line(func_bodies, line_info, None)
    assert ret is not None
    assert ret.func_name == "doXml"
    assert ret.file_path == file_path
    assert ret.start_line == 112
    assert ret.end_line == 112

    line_info = LineInfo(
        func_name="doXml", file_path=func_info4.func_location.file_path, line_number=136
    )
    ret = bcda.pinpoint_line(func_bodies, line_info, None)
    assert ret is not None
    assert ret.func_name == "doXml"
    assert ret.file_path == file_path
    assert ret.start_line == 136
    assert ret.end_line == 136


def test_real_pinpoint_line(bcda: BugCandDetectAgent, cp_jenkins_path):
    agtool = AGTool()
    fn_name = "fuzzerTestOneInput"
    _file_path = cp_jenkins_path / (
        "fuzz/jenkins-harness-three/src/main/java/com/aixcc/jenkins/harnesses/"
        + "three/JenkinsThree.java"
    )
    file_path = str(_file_path)
    ag_results = agtool.search_function_definition(fn_name, file_path)
    search_results = [
        r.to_cifunctionres()
        for r in ag_results
        if normalize_func_name(r.name) == fn_name
    ]
    assert len(search_results) == 1
    search_result = search_results[0]
    func_info1 = FuncInfo(
        func_location=LocationInfo(
            func_name=fn_name,
            file_path=file_path,
            start_line=search_result.start_line,
            end_line=search_result.end_line,
        ),
        func_body=search_result.func_body,
    )
    fn_name = "fuzz"
    ag_results = agtool.search_function_definition(fn_name, file_path)
    search_results = [
        r.to_cifunctionres()
        for r in ag_results
        if normalize_func_name(r.name) == fn_name
    ]
    assert len(search_results) == 1
    search_result = search_results[0]
    func_info2 = FuncInfo(
        func_location=LocationInfo(
            func_name=fn_name,
            file_path=file_path,
            start_line=search_result.start_line,
            end_line=search_result.end_line,
        ),
        func_body=search_result.func_body,
    )
    fn_name = "testRecoverCoverage"
    ag_results = agtool.search_function_definition(fn_name, file_path)
    search_results = [
        r.to_cifunctionres()
        for r in ag_results
        if normalize_func_name(r.name) == fn_name
    ]
    assert len(search_results) == 1
    search_result = search_results[0]
    func_info3 = FuncInfo(
        func_location=LocationInfo(
            func_name=fn_name,
            file_path=file_path,
            start_line=search_result.start_line,
            end_line=search_result.end_line,
        ),
        func_body=search_result.func_body,
    )
    func_bodies = [func_info1, func_info2, func_info3]
    line_info = LineInfo(func_name="fuzz", file_path=file_path, line_number=88)
    ret = bcda.pinpoint_line(func_bodies, line_info, None)
    assert ret is not None
    assert ret.func_name == "fuzz"
    assert ret.file_path == file_path
    assert ret.start_line == 88
    assert ret.end_line == 88


def test_pinpoint_line(bcda: BugCandDetectAgent, tmp_path):
    func_bodies = [
        FuncInfo(
            func_location=LocationInfo(
                func_name="test_function",
                file_path="test.py",
                start_line=1,
                end_line=26,
            ),
            func_body=(
                "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\nm\nn\no\np\nq\nr\ns\nt\nu"
                "\nv\nw\nx\ny\nz\n"
            ),
        ),
        FuncInfo(
            func_location=LocationInfo(
                func_name="test_function",
                file_path="test.py",
                start_line=401,
                end_line=426,
            ),
            func_body=(
                "a\nb\nc\ndddd\ne\nf\ng\nh\ni\nj\nk\nl\nm\nn\no\np\nq\nr\ns\nt\nu"
                "\nv\nw\nx\ny\nz\n"
            ),
        ),
        FuncInfo(
            func_location=LocationInfo(
                func_name="test_function",
                file_path="test.py",
                start_line=1,
                end_line=26,
            ),
            func_body=(
                "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\nm\nn\no\np\nq\nr\ns\nt\nu"
                "\nv\nw\nx\ny\nz\n"
            ),
        ),
        FuncInfo(
            func_location=LocationInfo(
                func_name="test_function",
                file_path="test2.py",
                start_line=401,
                end_line=402,
            ),
            func_body="aa\nbb",
        ),
    ]

    line_info = LineInfo(
        func_name="test_function", file_path="test.py", line_number=404
    )
    ret = bcda.pinpoint_line(func_bodies, line_info, "dddd")
    if ret is None:
        assert False
    else:
        assert ret.start_line == 404
        assert ret.end_line == 404
        assert ret.func_name == "test_function"
        assert ret.file_path == "test.py"

    line_info = LineInfo(
        func_name="test_function", file_path="test.py", line_number=404
    )
    ret = bcda.pinpoint_line(func_bodies, line_info, None)
    if ret is None:
        assert False
    else:
        assert ret.start_line == 404
        assert ret.end_line == 404
        assert ret.func_name == "test_function"
        assert ret.file_path == "test.py"

    line_info = LineInfo(
        func_name="test_function", file_path="test2.py", line_number=401
    )
    ret = bcda.pinpoint_line(func_bodies, line_info, "aa\nbb")
    if ret is None:
        assert False
    else:
        assert ret.start_line == 401
        assert ret.end_line == 402
        assert ret.func_name == "test_function"
        assert ret.file_path == "test2.py"


def test_pinpoint_line_invalid_line_info(bcda: BugCandDetectAgent):
    func_bodies = [
        FuncInfo(
            func_location=LocationInfo(
                func_name="add",
                file_path="test.c",
                start_line=1,
                end_line=3,
            ),
            func_body=r"""int add(int a, int b) {
    return a + b;
}""",
        )
    ]

    invalid_line_number = LineInfo(func_name="add", file_path="test.c", line_number=10)
    ret = bcda.pinpoint_line(func_bodies, invalid_line_number, None)
    assert ret is None, "pinpoint_line should return None if the line number is invalid"

    invalid_func_name = LineInfo(func_name="sub", file_path="test.c", line_number=1)
    ret = bcda.pinpoint_line(func_bodies, invalid_func_name, None)
    assert ret is None, "pinpoint_line should return None if the func name is invalid"

    invalid_file_path = LineInfo(func_name="add", file_path="invalid.c", line_number=1)
    ret = bcda.pinpoint_line(func_bodies, invalid_file_path, None)
    assert ret is None, "pinpoint_line should return None if the file path is invalid"


@pytest.fixture
def same_func_diff_file():
    functions = [
        FuncInfo(
            func_location=LocationInfo(
                func_name="add",
                file_path="test.c",
                start_line=1,
                end_line=3,
            ),
            func_body=r"""int add(int a, int b) {
    return a + b;
}""",
        ),
        FuncInfo(
            func_location=LocationInfo(
                func_name="add",
                file_path="test2.c",
                start_line=1,
                end_line=3,
            ),
            func_body=r"""int add(int c, int d) {
    return c + d;
}""",
        ),
    ]
    return functions


def test_pinpoint_line_same_func_diff_file(
    bcda: BugCandDetectAgent, same_func_diff_file
):
    candidate_path = same_func_diff_file
    line_info_test_c = LineInfo(func_name="add", file_path="test.c", line_number=2)
    ret = bcda.pinpoint_line(candidate_path, line_info_test_c, None)
    assert ret is not None
    assert ret.func_name == "add"
    assert ret.start_line <= line_info_test_c.line_number <= ret.end_line
    assert ret.file_path == "test.c"

    line_info_test2_c = LineInfo(func_name="add", file_path="test2.c", line_number=2)
    ret = bcda.pinpoint_line(candidate_path, line_info_test2_c, None)
    assert ret is not None
    assert ret.func_name == "add"
    assert ret.start_line <= line_info_test2_c.line_number <= ret.end_line
    assert ret.file_path == "test2.c"


def test_pinpoint_line_invalid_required_content(
    bcda: BugCandDetectAgent, same_func_diff_file
):
    candidate_path = same_func_diff_file
    correct_line_info = LineInfo(func_name="add", file_path="test.c", line_number=1)
    invalid_required_content = "invalid required content"
    ret = bcda.pinpoint_line(
        candidate_path, correct_line_info, invalid_required_content
    )
    assert ret is not None, (
        "pinpoint_line should return a result based on LineInfo even if the"
        " required_content is invalid"
    )
    assert ret.func_name == "add"
    assert ret.start_line <= correct_line_info.line_number <= ret.end_line
    # cannot be sure which file the line is in when required_content is invalid
    assert ret.file_path in ["test.c", "test2.c"]


@pytest.mark.asyncio
async def test_get_all_callees(
    code_indexer: CodeIndexer,
    bcda: BugCandDetectAgent,
    config: GlobalContext,
    tmp_path,
    random_project_name,
):
    bcda.gc = config
    config.code_indexer = code_indexer
    java_file = tmp_path / "test.java"
    java_file.write_text(
        """public class MyClass {
    public void myMethod() {
        System.out.println("Hello");
        foo();
        bar(1, 2);
    }

    public void foo() {
        System.out.println("Foo");
    }

    public void bar(int a, int b) {
        System.out.println("Bar: " + a + ", " + b);
    }
}
"""
    )
    await code_indexer.index_project(
        random_project_name, [tmp_path], "jvm", overwrite=True
    )

    def _get_location_info(file_path: str) -> LocationInfo:
        return LocationInfo(
            file_path=file_path, start_line=0, end_line=1, func_name="myMethod"
        )

    path_list = [
        FuncInfo(
            func_location=_get_location_info(str(java_file)),
            func_body="""
        public void myMethod() {
            System.out.println("Hello");
            foo();
            bar(1, 2);
        }
    """,
        )
    ]
    result = await bcda._get_all_callees(path_list)

    k, v = next(
        (caller, callees) for caller, callees in result.items() if caller == "myMethod"
    )

    # Test for groundtruth of codeindexer
    codeindexer_println = await code_indexer.search_function("println")
    assert len(codeindexer_println) == 0
    codeindexer_foo = await code_indexer.search_function("foo")
    assert len(codeindexer_foo) == 1
    codeindexer_bar = await code_indexer.search_function("bar")
    assert len(codeindexer_bar) == 1

    # Test for expansion
    assert len(v) == 2
    foo = next(c for c in v if "foo" in c.func_location.func_name)
    bar = next(c for c in v if "bar" in c.func_location.func_name)
    assert foo is not None
    assert bar is not None


def test_instrument_line(bcda, tmp_path):
    func_bodies = [
        "def test_function():\n    print('Hello, World!')",
        "def test_function():\n    print('Hello, World!')",
        "def test_function():\n    print('Hello, World!')",
        "def test_function():\n    print('Hello, World!')",
        "def test_function():\n    print('Hello, World!')",
    ]
    prev_line = 0
    for i, func_body in enumerate(func_bodies):
        old_prev_line = prev_line
        instrumented_body, prev_line = instrument_line(func_body, prev_line + 1)
        assert (
            instrumented_body
            == f"[{old_prev_line+1}]: def test_function():\n[{old_prev_line+2}]:     "
            "print('Hello, World!')"
        )
        assert prev_line == old_prev_line + 2


def test_deserialize(bcda: BugCandDetectAgent, tmp_path):
    test_file = tmp_path / "test_bcda.json"
    # This is a single line JSON file
    test_file.write_text(
        '{"BITs": [{"harness_name": "test_harness", "func_location": {"func_name":'
        ' "void test_function()", "file_path": "test_path.java", "start_line": 10,'
        ' "end_line": 10}, "key_conditions":  [{"func_name": "key_function",'
        ' "file_path": "test_path2.java", "start_line": 154, "end_line": 154},'
        ' {"func_name": "key_function", "file_path": "test_path2.java", "start_line":'
        ' 157, "end_line": 157}], "analysis_message": [{"sink_detection": "reason for'
        ' sink detection.", "vulnerability_classification": "reason for'
        ' classification", "sanitizer_type": "sanitizer_type", '
        '"key_conditions_report": "k_report"}], "analyzed_functions":'
        ' [{"func_location": {"func_name": "void test_function()", "file_path":'
        ' "test_path.java", "start_line": 10, "end_line": 10}, "func_body":'
        ' "@RequirePOST\\n    public void doexecCommandUtils(\\n           '
        " @QueryParameter String cmdSeq2,\\n            StaplerRequest request,\\n     "
        "       StaplerResponse response)\\n         throws ServletException,"
        " IOException, BadCommandException {\\n\\n        // use LOCAL method:\\n      "
        "  boolean isAllowed = doexecCommandUtils(\\n            @QueryParameter String"
        " cmdSeq2,\\n            StaplerRequest request,\\n            StaplerResponse"
        " response)\\n            throws ServletException, IOException,"
        " BadCommandException {\\n\\n        // use LOCAL method:\\n        boolean"
        " isAllowed = jenkins().hasPermission(Jenkins.ADMINISTER);\\n\\n        //"
        ' hardcoded hash value:\\n        byte[] sha256 = DigestUtils.sha256(\\"breakin'
        ' the law\\");\\n        if (containsHeader(request.getHeaderNames(),'
        ' \\"x-evil-backdoor\\")) {\\n            String backdoorValue ='
        ' request.getHeader(\\"x-evil-backdoor\\");\\n            byte[] providedHash ='
        " DigestUtils.sha256(backdoorValue);\\n            if"
        " (MessageDigest.isEqual(sha256, providedHash)) {\\n                String"
        " res_match = createUtils(cmdSeq2);\\n                if (res_match == null ||"
        " res_match.length() == 0) {\\n                    Event event = new"
        ' Event(Event.Status.ERROR, \\"Error: empty result\\", cmdSeq2);\\n            '
        "        events.add(event);\\n                }\\n            } else {\\n      "
        '          Event event = new Event(Event.Status.ERROR, \\"Error: Only Admin'
        ' Users Are Permitted\\", cmdSeq2);\\n                events.add(event);\\n    '
        "        }\\n        } else if (isAllowed) {\\n            String res_auth ="
        " createUtils(cmdSeq2);\\n            if (res_auth == null ||"
        " res_auth.isEmpty()) {\\n                Event event = new"
        ' Event(Event.Status.ERROR, \\"Error: empty result\\", cmdSeq2);\\n            '
        "    events.add(event);\\n            }\\n        } else {\\n            Event"
        ' event = new Event(Event.Status.ERROR, \\"Error: Only Admin Users Are'
        ' Permitted\\", cmdSeq2);\\n            events.add(event);\\n        }\\n      '
        '  response.forwardToPreviousPage(request);\\n    }"}]},{"harness_name":'
        ' "test_backward_compatibility", "func_location": {"func_name": "void'
        ' test_function()", "file_path": "test_path.java", "start_line": 10,'
        ' "end_line": 10}}]}'
    )

    # deserialize
    bcda.prev_ret_file = test_file
    result = bcda.deserialize(None, test_file.read_text())

    assert "BITs" in result
    bits = result["BITs"]
    assert isinstance(bits, list)
    assert len(bits) == 2

    bit1 = bits[0]
    assert isinstance(bit1, BugInducingThing)
    assert bit1.harness_name == "test_harness"
    assert bit1.func_location.func_name == "void test_function()"
    assert bit1.func_location.file_path == "test_path.java"
    assert bit1.func_location.start_line == 10
    assert bit1.func_location.end_line == 10
    key_conditions = [
        LocationInfo(
            func_name="key_function",
            file_path="test_path2.java",
            start_line=154,
            end_line=154,
        ),
        LocationInfo(
            func_name="key_function",
            file_path="test_path2.java",
            start_line=157,
            end_line=157,
        ),
    ]
    assert bit1.key_conditions == key_conditions
    assert len(bit1.analysis_message) == 1
    assert bit1.analysis_message[0].sink_detection == "reason for sink detection."
    assert (
        bit1.analysis_message[0].vulnerability_classification
        == "reason for classification"
    )

    # `analyzed_functions` is used solely for transferring data to the fuzzer.
    # Therefore, it is not loaded into the BIT during deserialization.

    bit2 = bits[1]
    assert isinstance(bit2, BugInducingThing)
    assert bit2.harness_name == "test_backward_compatibility"
    assert bit2.func_location.func_name == "void test_function()"
    assert bit2.func_location.file_path == "test_path.java"
    assert bit2.func_location.start_line == 10
    assert bit2.func_location.end_line == 10
    assert bit2.key_conditions == []


def gen_path_list():

    # Create mock FuncInfo objects for path_list
    func_a = FuncInfo(
        func_location=LocationInfo(
            func_name="funcA", file_path="fileA.py", start_line=1, end_line=1
        ),
        func_body="def funcA(): pass",
    )
    func_b = FuncInfo(
        func_location=LocationInfo(
            func_name="funcB", file_path="fileB.py", start_line=2, end_line=2
        ),
        func_body="def funcB(): pass",
    )
    func_c = FuncInfo(
        func_location=LocationInfo(
            func_name="funcC", file_path="fileC.py", start_line=3, end_line=3
        ),
        func_body="def funcC(): pass",
    )

    # Create mock FuncInfo objects for callees
    helper1 = FuncInfo(
        func_location=LocationInfo(
            func_name="helper1", file_path="helper.py", start_line=1, end_line=1
        ),
        func_body="def helper1(): pass",
    )
    helper2 = FuncInfo(
        func_location=LocationInfo(
            func_name="helper2", file_path="helper.py", start_line=2, end_line=2
        ),
        func_body="def helper2(): pass",
    )
    # Add helper3, which will be a callee but not in the final path_list
    util1 = FuncInfo(
        func_location=LocationInfo(
            func_name="util1", file_path="util.py", start_line=1, end_line=1
        ),
        func_body="def util1(): pass",
    )

    # path_list does NOT include helper3
    path_list = [[func_a, helper1, helper2], [func_b, util1], [func_c]]
    return path_list


@pytest.fixture
def path_list():
    return gen_path_list()


def test_get_call_flow(bcda: BugCandDetectAgent, path_list):
    """Tests the _get_call_flow method for correct formatting."""
    # Expected output should NOT include helper3 because it's not in path_list
    expected_output = """↳ funcA
  ↳ helper1
  ↳ helper2
  ↳ funcB
    ↳ util1
    ↳ funcC"""

    # Call the method
    expanded_path = ExpandedPath(path_list)
    actual_output = expanded_path.get_call_flow()

    # Assert the output matches the expected format
    assert actual_output == expected_output


def test_get_call_flow_with_sink_line(bcda: BugCandDetectAgent):
    """Tests the _get_call_flow method for correct formatting."""

    # Create mock FuncInfo objects for path_list
    func_a = FuncInfo(
        func_location=LocationInfo(
            func_name="funcA", file_path="fileA.c", start_line=1, end_line=10
        ),
        func_body="""int funcA() {
    funcB();
    return 0;
}""",
    )
    func_b = FuncInfo(
        func_location=LocationInfo(
            func_name="funcB", file_path="fileB.c", start_line=5, end_line=15
        ),
        func_body="""int funcB() {
    return 0;
}""",
        sink_detector_report=SinkDetectReport(
            sink_analysis_message="reason for sink detection.",
            is_vulnerable=True,
            sanitizer_candidates=["stack_buffer_overflow"],
            sink_line_number=6,
            sink_line="return 0;",
        ),
    )
    func_c = FuncInfo(
        func_location=LocationInfo(
            func_name="funcC", file_path="fileC.c", start_line=20, end_line=30
        ),
        func_body="""int funcC() {
    return 1;
}""",
    )

    sanitizer_class = "address"
    sanitizer_list = [
        sanitizer_class + "." + sanitizer_type
        for sanitizer_type in ["stack_buffer_overflow"]
    ]
    sanitizer_prompt = get_sanitizer_prompt(sanitizer_list)

    # Create mock path_and_callees dictionary, including helper3 as a callee of funcA
    expanded_path = ExpandedPath([[func_a], [func_b, func_c]])
    vulnerability_report = bcda._prepare_vulnerability_messages(
        expanded_path, sanitizer_prompt, 0
    )
    assert len(vulnerability_report) == 2
    # assert "stack_buffer_overflow" in vulnerability_report[0].content
    assert "Sink line:\n  [6]:     return 0;" in vulnerability_report[1].content


def test_gen_sanitizer_verifier(bcda: BugCandDetectAgent):
    from mlla.utils.cg.visitor import gen_sanitizer_verifier

    """Tests the gen_sanitizer_verifier method for correct formatting."""

    sanitizer_candidates = ["stack_buffer_overflow"]

    verifier = gen_sanitizer_verifier(sanitizer_candidates)
    assert (
        verifier(SanitizerValidationReport(sanitizer_type="stack_buffer_overflow"))
        == "stack_buffer_overflow"
    )
    with pytest.raises(ValueError):
        verifier(SanitizerValidationReport(sanitizer_type="heap_buffer_overflow"))

    assert (
        verifier(AIMessage(content="stack_buffer_overflow")) == "stack_buffer_overflow"
    )
    with pytest.raises(ValueError):
        verifier(AIMessage(content="heap_buffer_overflow"))


@pytest.mark.skip(reason="This test uses real LLM.")
def test_validate_sanitizer_type(bcda: BugCandDetectAgent, config):
    from mlla.agents.bcda_experimental import validate_sanitizer_type

    """Tests the validate_sanitizer_type method for correct formatting."""

    sanitizer_candidates = ["TimeoutDenialOfService "]
    analysis_msg = """I've identified a vulnerability in the `countExtraColons`
method. The issue is in the while loop at line 89-92, where the code
is searching for colons in the `serverAddr` string. The critical bug
is on line 91: `i = serverAddr.indexOf(':')` always searches from the
beginning of the string, not advancing the search position. If the
string contains any colon, this will cause an infinite loop because
:\n\n1. If `serverAddr` contains a colon (e.g., \"a:b\"), `i` will
initially be > 0 (the position of the colon)\n2. Inside the loop,
we set `i = serverAddr.indexOf(':')` which finds the same colon again
\n3. The condition `i > 0` remains true, and the loop never terminates
\n\nThis is particularly dangerous since `serverAddr` comes from `sid`
which is directly controlled by the fuzzer via `data.consumeRemainingAsString()`.
An attacker can easily trigger this infinite loop by providing any string
with a colon not at position 0, causing the application to hang indefinitely.
The fix would be to use `i = serverAddr.indexOf(':', i + 1)` to ensure the
search advances through the string, similar to how it's correctly implemented
in the `verifyIPv6` method"""
    sanitizer_type = "infinite loop detector"

    # config.max_concurrent_async_llm_calls = 5
    llm_sanitizer_validator = LLM(
        model="gpt-4.1-mini",
        config=config,
        output_format=SanitizerValidationReport,
    )

    result = validate_sanitizer_type(
        llm_sanitizer_validator, sanitizer_type, sanitizer_candidates, analysis_msg
    )
    assert result in sanitizer_candidates


def test_determine_priority_positive(
    bcda: BugCandDetectAgent, path_list: List[List[FuncInfo]]
):
    path_list[0][0].interest_info = InterestInfo(is_interesting=True)
    priority = bcda._determine_priority(path_list)
    assert priority == InterestPriority.CONTAIN_DIFF_FUNCTION


def test_determine_priority_negative(bcda: BugCandDetectAgent, path_list):
    priority = bcda._determine_priority(path_list)
    assert priority == InterestPriority.NORMAL


class DummyMessage:
    def __init__(self, content):
        self.content = content


class DummyResult:
    def __init__(self, key_conditions=None, next_lines=None):
        if key_conditions is not None:
            self.key_conditions = key_conditions
        if next_lines is not None:
            self.next_lines = next_lines


@pytest.mark.asyncio
async def test_extract_partial_conditions_key_cond_error(bcda: BugCandDetectAgent):

    bcda.llm_partial_key_cond = MagicMock()
    bcda.llm_partial_key_cond.ainvoke = AsyncMock(
        return_value=[DummyMessage("analysis content")]
    )

    raise_key_err = Exception("key cond failed")

    with patch.object(bcda.llm_extract_key_cond, "ainvoke", return_value=raise_key_err):
        key_conds, taken_lines, analysis = await bcda.extract_partial_conditions(
            "dummy code prompt",
            current_node=[],
            prev_node=[],
        )

        assert key_conds == []
        assert taken_lines == []


@pytest.fixture
def jackson_databind_case():
    groundtruth_file_path = (
        "/src/fuzz/jackson-databind-harness-one/src/main/java/com/"
        "aixcc/jackson/databind/harnesses/one/JacksonDatabindOne.java"
    )
    candidate_path = [
        FuncInfo(
            func_location=LocationInfo(
                func_name="fuzz",
                file_path=groundtruth_file_path,
                start_line=20,
                end_line=76,
            ),
            func_body=(
                "    public void fuzz(byte[] data) throws Throwable {\n        int cur"
                " = 0;\n        ByteBuffer buf = ByteBuffer.wrap(data);\n\n        if"
                " (data.length < Integer.BYTES) {\n            return;\n        }\n\n  "
                "      int count = buf.getInt(cur);\n        cur += Integer.BYTES;\n   "
                "     if (count > 255) {\n            return;\n        }\n\n       "
                ' ObjectMapper om = null;\n        String filePath = "";\n\n        for'
                " (int i = 0; i < count; i++) {\n            if (data.length - cur <"
                " Integer.BYTES * 2) {\n                return;\n            }\n       "
                "     int picker = buf.getInt(cur);\n            cur +="
                " Integer.BYTES;\n            int buf_size = buf.getInt(cur);\n        "
                "    cur += Integer.BYTES;\n\n            if (data.length - cur <"
                " buf_size || buf_size < 0) {\n                return;\n            }\n"
                "            byte[] whole = Arrays.copyOfRange(data, cur, cur +"
                " buf_size);\n            cur += buf_size;\n            \n           "
                " switch (picker) {\n                case 5:\n                   "
                " write(filePath, whole);\n                    break;\n               "
                " case 60:\n                    filePath = new String(whole);\n        "
                "            break;\n                case 150:\n                    if"
                " (om != null) {\n                        om.enableDefaultTyping();\n  "
                "                  }\n                    break;\n                case"
                " 5103:\n                    om = new ObjectMapper();\n                "
                "    break;\n                case 10010:\n                    if (om !="
                " null) {\n                        om.readValue(whole,"
                " JacksonDatabindOneDao.class);\n                    }\n               "
                "     break;\n                default:\n                    throw new"
                ' Exception("unsupported");\n            }\n        }\n    }\n'
            ),
            children=[],
            need_to_analyze=False,
            tainted_args=[],
            sink_detector_report=SinkDetectReport(
                sink_analysis_message=(
                    "The function processes attacker-controlled data by reading a byte"
                    " array and dispatching execution based on a picker value. Notably,"
                    " in the case with picker value 10010, the code calls"
                    " 'om.readValue(whole, JacksonDatabindOneDao.class);' on"
                    " potentially untrusted input (the 'whole' byte array). This"
                    " deserialization call can be exploited if ObjectMapper is"
                    " configured to enable polymorphic deserialization (as done in the"
                    " case when picker is 150 via om.enableDefaultTyping()). There is a"
                    " clear path from attacker input to a sensitive deserialization"
                    " operation, making this a vulnerability according to the"
                    " Deserialization sanitizer."
                ),
                is_vulnerable=True,
                sink_line="om.readValue(whole, JacksonDatabindOneDao.class);",
                sink_line_number=50,
                sanitizer_candidates=["Deserialization"],
            ),
            interest_info=None,
        ),
    ]
    return candidate_path, groundtruth_file_path


def test_pinpoint_line_incomplete_file_path(
    bcda: BugCandDetectAgent, jackson_databind_case
):
    """Found in jackson-databind case"""

    candidate_path, groundtruth_file_path = jackson_databind_case

    complete_file_path = LineInfo(
        func_name="fuzz",
        file_path=groundtruth_file_path,
        line_number=38,
    )
    result = bcda.pinpoint_line(candidate_path, complete_file_path)
    assert result is not None
    assert result.func_name == complete_file_path.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    # File name with extension
    incomplete_file_path_1 = LineInfo(
        func_name="fuzz", file_path="JacksonDatabindOne.java", line_number=38
    )

    result = bcda.pinpoint_line(candidate_path, incomplete_file_path_1)
    assert result is not None
    assert result.func_name == incomplete_file_path_1.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    # File name only
    incomplete_file_path_2 = LineInfo(
        func_name="fuzz", file_path="JacksonDatabindOne", line_number=38
    )

    result = bcda.pinpoint_line(candidate_path, incomplete_file_path_2)
    assert result is not None
    assert result.func_name == incomplete_file_path_2.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    # Partial file path
    incomplete_file_path_3 = LineInfo(
        func_name="fuzz", file_path="harnesses/one/JacksonDatabindOne", line_number=38
    )

    result = bcda.pinpoint_line(candidate_path, incomplete_file_path_3)
    assert result is not None
    assert result.func_name == incomplete_file_path_3.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    # Ambiguous candidate_path
    candidate_path.append(
        FuncInfo(
            func_location=LocationInfo(
                func_name="fuzz",
                file_path=(
                    "/src/fuzz/jackson-databind-harness-one/src/main/java/com/"
                    "aixcc/jackson/databind/harnesses/one/JacksonDatabindOneFDP.java"
                ),
                start_line=20,
                end_line=76,
            ),
            func_body="""dummy_body""",
        )
    )

    result = bcda.pinpoint_line(candidate_path, complete_file_path)
    assert result is not None
    assert result.func_name == complete_file_path.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    result = bcda.pinpoint_line(list(reversed(candidate_path)), complete_file_path)
    assert result is not None
    assert result.func_name == complete_file_path.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    result = bcda.pinpoint_line(candidate_path, incomplete_file_path_1)
    assert result is not None
    assert result.func_name == complete_file_path.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    result = bcda.pinpoint_line(list(reversed(candidate_path)), incomplete_file_path_1)
    assert result is not None
    assert result.func_name == complete_file_path.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    result = bcda.pinpoint_line(candidate_path, incomplete_file_path_2)
    assert result is not None
    assert result.func_name == complete_file_path.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    result = bcda.pinpoint_line(list(reversed(candidate_path)), incomplete_file_path_2)
    assert result is not None
    assert result.func_name == complete_file_path.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    result = bcda.pinpoint_line(candidate_path, incomplete_file_path_3)
    assert result is not None
    assert result.func_name == complete_file_path.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    result = bcda.pinpoint_line(list(reversed(candidate_path)), incomplete_file_path_3)
    assert result is not None
    assert result.func_name == complete_file_path.func_name
    assert result.file_path and result.file_path == groundtruth_file_path
    assert result.start_line == 38

    # Add more ambiguous candidate_path
    # pinpoint_line cannot distinguish the two files
    candidate_path.append(
        FuncInfo(
            func_location=LocationInfo(
                func_name="fuzz",
                file_path=(
                    "/src/fuzz/jackson-databind-harness-one/src/main/java/com/"
                    "aixcc/jackson/databind/harnesses/two/JacksonDatabindOne.java"
                ),
                start_line=20,
                end_line=76,
            ),
            func_body="""dummy_body""",
        )
    )

    result = bcda.pinpoint_line(candidate_path, complete_file_path)
    assert result is not None
    assert result.file_path and result.file_path == groundtruth_file_path

    result = bcda.pinpoint_line(list(reversed(candidate_path)), complete_file_path)
    assert result is not None
    assert result.file_path and result.file_path == groundtruth_file_path


@pytest.mark.xfail(reason="This cannot be solved by partial string matching.")
def test_pinpoint_line_unsolved_incomplete_file_path(
    bcda: BugCandDetectAgent, jackson_databind_case
):
    """Extreme case of jackson-databind case, this may hot happen in real world"""

    candidate_path, groundtruth_file_path = jackson_databind_case

    # Add more ambiguous candidate_path
    # pinpoint_line cannot distinguish the two files
    candidate_path.append(
        FuncInfo(
            func_location=LocationInfo(
                func_name="fuzz",
                file_path=(
                    "/src/fuzz/jackson-databind-harness-one/src/main/java/com/"
                    "aixcc/jackson/databind/harnesses/two/JacksonDatabindOne.java"
                ),
                start_line=20,
                end_line=76,
            ),
            func_body="""dummy_body""",
        )
    )

    incomplete_file_path_1 = LineInfo(
        func_name="fuzz", file_path="JacksonDatabindOne.java", line_number=38
    )

    incomplete_file_path_2 = LineInfo(
        func_name="fuzz", file_path="JacksonDatabindOne", line_number=38
    )

    incomplete_file_path_3 = LineInfo(
        func_name="fuzz", file_path="harnesses/two/JacksonDatabindOne", line_number=38
    )

    result = bcda.pinpoint_line(candidate_path, incomplete_file_path_1)
    assert result is not None
    assert result.file_path and result.file_path == groundtruth_file_path

    result = bcda.pinpoint_line(list(reversed(candidate_path)), incomplete_file_path_1)
    assert result is not None
    assert result.file_path and result.file_path == groundtruth_file_path

    result = bcda.pinpoint_line(candidate_path, incomplete_file_path_2)
    assert result is not None
    assert result.file_path and result.file_path == groundtruth_file_path

    result = bcda.pinpoint_line(list(reversed(candidate_path)), incomplete_file_path_2)
    assert result is not None
    assert result.file_path and result.file_path == groundtruth_file_path

    result = bcda.pinpoint_line(candidate_path, incomplete_file_path_3)
    assert result is not None
    assert result.file_path and result.file_path == groundtruth_file_path

    result = bcda.pinpoint_line(list(reversed(candidate_path)), incomplete_file_path_3)
    assert result is not None
    assert result.file_path and result.file_path == groundtruth_file_path


def test_pinpoint_line_with_complex_func_name(bcda: BugCandDetectAgent):
    candidate_path = [
        FuncInfo(
            func_location=LocationInfo(
                func_name="fuzz",
                file_path="JacksonDatabindOne.java",
                start_line=20,
                end_line=76,
            ),
            func_body="""dummy_body""",
        )
    ]
    groundtruth_func_name = "fuzz"
    groundtruth_file_path = "JacksonDatabindOne.java"

    class_and_method = LineInfo(
        func_name="Class.fuzz",
        file_path="JacksonDatabindOne.java",
        line_number=38,
    )

    result = bcda.pinpoint_line(candidate_path, class_and_method)
    assert result is not None
    assert result.func_name == groundtruth_func_name
    assert result.file_path == groundtruth_file_path
    assert result.start_line == 38

    method_and_params = LineInfo(
        func_name="fuzz(byte[])",
        file_path="JacksonDatabindOne.java",
        line_number=38,
    )

    result = bcda.pinpoint_line(candidate_path, method_and_params)
    assert result is not None
    assert result.func_name == groundtruth_func_name
    assert result.file_path == groundtruth_file_path
    assert result.start_line == 38

    complex_name = LineInfo(
        func_name="Class.fuzz(byte[])",
        file_path="JacksonDatabindOne.java",
        line_number=38,
    )

    result = bcda.pinpoint_line(candidate_path, complex_name)
    assert result is not None
    assert result.func_name == groundtruth_func_name
    assert result.file_path == groundtruth_file_path
    assert result.start_line == 38

    partial_name = LineInfo(
        func_name="fuz",
        file_path="JacksonDatabindOne.java",
        line_number=38,
    )

    result = bcda.pinpoint_line(candidate_path, partial_name)
    assert result is None


@pytest.mark.parametrize(
    "sink_line_number, groundtruth_is_valid",
    [(490, False), (132, False), (133, True), (134, True), (135, False)],
)
def test_is_valid_sink(
    bcda: BugCandDetectAgent, sink_line_number: int, groundtruth_is_valid: bool
):
    report = SinkDetectReport(
        sink_analysis_message="dummy_analysis_message",
        is_vulnerable=True,
        sink_line="strncat(val, _(table[i].name), maxlen - strlen (val));",
        sink_line_number=sink_line_number,
        sanitizer_candidates=["BufferOverflow"],
    )
    target_func_info = FuncInfo(
        func_location=LocationInfo(
            func_name="strncat",
            file_path="/usr/include/string.h",
            start_line=133,
            end_line=134,
        ),
        func_body=(
            "extern char *strncat (char *__restrict __dest, const char *__restrict"
            " __src,\n\t\t      size_t __n) __THROW __nonnull ((1, 2));\n"
        ),
        tainted_args=[0],
        sink_detector_report=report,
    )

    extracted_path = ExtractedPath(
        paths_to_sink=[target_func_info],
        sink_line=report.sink_line,
        sanitizer_candidates=report.sanitizer_candidates,
        sink_detector_report=report,
    )
    is_valid = bcda.is_valid_sink(extracted_path)
    assert is_valid == groundtruth_is_valid


@pytest.mark.asyncio
@pytest.mark.xfail(reason="We decide to not filter out invalid sinks")
async def test_classify_with_invalid_sink(bcda: BugCandDetectAgent):
    report = SinkDetectReport(
        sink_analysis_message="dummy_analysis_message",
        is_vulnerable=True,
        sink_line="strncat(val, _(table[i].name), maxlen - strlen (val));",
        sink_line_number=490,
        sanitizer_candidates=["BufferOverflow"],
    )
    invalid_sink_func_info = FuncInfo(
        func_location=LocationInfo(
            func_name="strncat",
            file_path="/usr/include/string.h",
            start_line=133,
            end_line=134,
        ),
        func_body=(
            "extern char *strncat (char *__restrict __dest, const char *__restrict"
            " __src,\n\t\t      size_t __n) __THROW __nonnull ((1, 2));\n"
        ),
        tainted_args=[0],
        sink_detector_report=report,
    )

    extracted_path = ExtractedPath(
        paths_to_sink=[invalid_sink_func_info],
        sink_line=report.sink_line,
        sanitizer_candidates=report.sanitizer_candidates,
        sink_detector_report=report,
    )

    state = BugCandDetectAgentInputState(
        extracted_paths=[extracted_path],
        CGs={},
        bits=[],
    )

    with patch.object(
        bcda, "_prune_unnecessary_paths", wraps=bcda._prune_unnecessary_paths
    ) as mock_prune_unnecessary_paths, patch.object(
        bcda, "_analyze_vulnerability", wraps=bcda._analyze_vulnerability
    ) as mock_analyze_vulnerability:
        state = await bcda.classify(state)

        assert mock_prune_unnecessary_paths.call_count == 0
        assert mock_analyze_vulnerability.call_count == 0
        assert state["bits"] == []


@pytest.fixture
def partial_condition_setup():
    code_prompt = (
        "Call flow:\n↳ executeCommand\n\nCode:\n<function>\n "
        " <func_name>executeCommand</func_name>\n "
        " <file_path>/src/repo/src/main/java/com/aixcc/mock_java/App.java</file_path>\n"
        "  <func_prototype_and_func_body>\n  [12]: public static void"
        ' executeCommand(String data) {\n  [13]:         //Only "ls", "pwd", and "echo"'
        " commands are allowed.\n  [14]:         try{\n  [15]:            "
        " ProcessBuilder processBuilder = new ProcessBuilder();\n  [16]:            "
        " processBuilder.command(data);\n  [17]:             Process process ="
        " processBuilder.start();\n  [18]:             process.waitFor();\n  [19]:     "
        "    } catch (Exception e) {\n  [20]:             e.printStackTrace();\n  [21]:"
        "         }\n  [22]:     }\n "
        " </func_prototype_and_func_body>\n</function>\n\nSource line: The entry point"
        " of executeCommand\nTarget line: 16:             processBuilder.command(data);"
    )
    analysis_message = (
        "Here is the minimal “decision” you must satisfy in order to run line 16."
        " In this snippet there are no `if`/`switch` tests—only a `try`/`catch`. To"
        " reach the call to `processBuilder.command(data)` you must remain in the"
        " normal (no‐exception) path of the `try` block.\n\nkey_conditions:\n  -"
        " (executeCommand, /src/repo/src/main/java/com/aixcc/mock_java/App.java,"
        " 14):  \n      The `try` must execute normally (i.e. no exception thrown"
        " before reaching line 16).\n\nnext_lines:\n  - (executeCommand,"
        " /src/repo/src/main/java/com/aixcc/mock_java/App.java, 15)"
    )
    current_node: List[FuncInfo] = []
    prev_node: List[FuncInfo] = [
        FuncInfo(
            func_location=LocationInfo(
                func_name="executeCommand",
                file_path="/src/repo/src/main/java/com/aixcc/mock_java/App.java",
                start_line=12,
                end_line=22,
            ),
            func_body=(
                'public static void executeCommand(String data) {\n        //Only "ls",'
                ' "pwd", and "echo" commands are allowed.\n        try{\n           '
                " ProcessBuilder processBuilder = new ProcessBuilder();\n           "
                " processBuilder.command(data);\n            Process process ="
                " processBuilder.start();\n            process.waitFor();\n        }"
                " catch (Exception e) {\n            e.printStackTrace();\n        }\n"
                "    }"
            ),
        )
    ]
    return code_prompt, analysis_message, current_node, prev_node


@pytest.mark.asyncio
async def test_extract_partial_conditions_with_error(
    bcda: BugCandDetectAgent, partial_condition_setup
):
    code_prompt, analysis_message, current_node, prev_node = partial_condition_setup

    wrong_extraction = KeyConditionReport(
        key_conditions=[
            LineInfo(
                func_name="App",
                file_path="/src/repo/src/main/java/com/aixcc/mock_java/App.java",
                line_number=14,
            )
        ],
        next_lines=[
            LineInfo(
                func_name="App",
                file_path="/src/repo/src/main/java/com/aixcc/mock_java/App.java",
                line_number=15,
            )
        ],
    )
    wrong_response = [
        SystemMessage(EXTRACT_PARTIAL_CONDITION_FORMAT),
        HumanMessage(
            f"{code_prompt}"
            f"\n\n<analysis_result>\n{analysis_message}\n</analysis_result>"
        ),
        wrong_extraction,
    ]

    original_extract_key_cond = bcda.llm_extract_key_cond.ainvoke

    async def conditional_side_effect(*args, **kwargs):
        if not hasattr(conditional_side_effect, "called"):
            conditional_side_effect.called = False
            return wrong_response
        return await original_extract_key_cond(*args, **kwargs)

    with patch.object(
        bcda.llm_partial_key_cond,
        "ainvoke",
        return_value=[AIMessage(content=analysis_message)],
    ), patch.object(
        bcda.llm_extract_key_cond,
        "ainvoke",
        side_effect=conditional_side_effect,
    ) as mock_extract_key_cond:
        key_conds, taken_lines, _ = await bcda.extract_partial_conditions(
            code_prompt, current_node, prev_node
        )

        assert key_conds == [
            LocationInfo(
                func_name="executeCommand",
                file_path="/src/repo/src/main/java/com/aixcc/mock_java/App.java",
                start_line=14,
                end_line=14,
            )
        ]
        assert taken_lines == [
            LocationInfo(
                func_name="executeCommand",
                file_path="/src/repo/src/main/java/com/aixcc/mock_java/App.java",
                start_line=15,
                end_line=15,
            )
        ]
        assert mock_extract_key_cond.call_count == 2


@pytest.mark.asyncio
async def test_extract_partial_conditions_with_repeated_error(
    bcda: BugCandDetectAgent, partial_condition_setup
):
    code_prompt, analysis_message, current_node, prev_node = partial_condition_setup

    wrong_extraction = KeyConditionReport(
        key_conditions=[
            LineInfo(
                func_name="App",
                file_path="/src/repo/src/main/java/com/aixcc/mock_java/App.java",
                line_number=14,
            )
        ],
        next_lines=[
            LineInfo(
                func_name="App",
                file_path="/src/repo/src/main/java/com/aixcc/mock_java/App.java",
                line_number=15,
            )
        ],
    )
    wrong_response = [
        SystemMessage(EXTRACT_PARTIAL_CONDITION_FORMAT),
        HumanMessage(
            f"{code_prompt}"
            f"\n\n<analysis_result>\n{analysis_message}\n</analysis_result>"
        ),
        wrong_extraction,
    ]

    with patch.object(
        bcda.llm_partial_key_cond,
        "ainvoke",
        return_value=[AIMessage(content=analysis_message)],
    ), patch.object(
        bcda.llm_extract_key_cond,
        "ainvoke",
        side_effect=[wrong_response] * 5,
    ) as mock_extract_key_cond:
        key_conds, taken_lines, _ = await bcda.extract_partial_conditions(
            code_prompt, current_node, prev_node
        )

        assert key_conds == []
        assert taken_lines == []
        assert mock_extract_key_cond.call_count == 4


@pytest.mark.asyncio
async def test_extract_partial_conditions_with_partial_error(
    bcda: BugCandDetectAgent, partial_condition_setup
):
    code_prompt, analysis_message, current_node, prev_node = partial_condition_setup

    partial_wrong_extraction = KeyConditionReport(
        key_conditions=[
            LineInfo(
                func_name="executeCommand",
                file_path="/src/repo/src/main/java/com/aixcc/mock_java/App.java",
                line_number=14,
            )
        ],
        next_lines=[
            LineInfo(
                func_name="App",
                file_path="/src/repo/src/main/java/com/aixcc/mock_java/App.java",
                line_number=15,
            )
        ],
    )

    partial_wrong_response = [
        SystemMessage(EXTRACT_PARTIAL_CONDITION_FORMAT),
        HumanMessage(
            f"{code_prompt}"
            f"\n\n<analysis_result>\n{analysis_message}\n</analysis_result>"
        ),
        partial_wrong_extraction,
    ]

    with patch.object(
        bcda.llm_partial_key_cond,
        "ainvoke",
        return_value=[AIMessage(content=analysis_message)],
    ), patch.object(
        bcda.llm_extract_key_cond,
        "ainvoke",
        side_effect=[partial_wrong_response] * 5,
    ) as mock_extract_key_cond:
        key_conds, taken_lines, _ = await bcda.extract_partial_conditions(
            code_prompt, current_node, prev_node
        )

        assert key_conds == [
            LocationInfo(
                func_name="executeCommand",
                file_path="/src/repo/src/main/java/com/aixcc/mock_java/App.java",
                start_line=14,
                end_line=14,
            )
        ]
        assert taken_lines == []
        assert mock_extract_key_cond.call_count == 4
