import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from loguru import logger
from multilspy import LanguageServer
from multilspy.multilspy_config import MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

from mlla.agents.mcga import (
    TOOL_MODEL,
    CalleeRes,
    ExpectedException,
    MakeCallGraphAgent,
    MakeCallGraphOverallState,
    MCGASinkDetectReport,
)
from mlla.utils.bit import LocationInfo
from mlla.utils.cg import FuncInfo
from mlla.utils.context import GlobalContext
from mlla.utils.cp import CP, init_cp_repo, sCP, sCP_Harness
from mlla.utils.llm_tools.astgrep import AGTool
from tests.dummy_context import DummyContext


def test_libpng(cp_libpng_path) -> None:
    init_cp_repo(cp_libpng_path)
    ag_tool = AGTool()
    png_read = cp_libpng_path / "repo/pngread.c"
    results = ag_tool.search_function_definition("png_read_end", png_read.as_posix())
    assert len(results) > 0

    for r in results:
        logger.info(f"result: {r}")


@pytest.mark.parametrize("setup_lsp", [["aixcc/jvm/jenkins"]], indirect=True)
@pytest.mark.asyncio
@pytest.mark.skip(reason="Skip for CI")
async def test_loop_mcga(setup_lsp: dict, cp_jenkins_path: Path) -> None:
    cp_jenkins_path = cp_jenkins_path.resolve()
    target_fn = (
        "createUtils",
        (
            cp_jenkins_path
            / (
                "repo/plugins/"
                + "pipeline-util-plugin/src/main/java/io/jenkins/plugins/UtilPlug/"
                + "UtilMain.java"
            )
        ).as_posix(),
        """    String createUtils(String cmd) throws BadCommandException {
        if (cmd == null || cmd.trim().isEmpty()) {
            throw new BadCommandException("Invalid command line");
        }

        String[] cmds = {cmd};

        try {
            ProcessBuilder processBuilder;
            processBuilder = new ProcessBuilder(cmds);
            Process process = null;
            try {
                process = processBuilder.start();
            } catch (IOException ignored) {
                // Ignored, but the sanitizer should still throw an exception.
            }

            // Capture output
            if (process != null) {
                String output = captureOutput(process);
                // Print output for POV
                // System.out.println(output);
                Event event = new Event(Event.Status.SUCCESS, output, cmd);
                events.add(event);

                // Wait for the process to complete
                int exitCode = process.waitFor();
                return cmd;
            } else {
                return null;
            }
        } catch (IOException | InterruptedException e) {
            e.printStackTrace();
            return null;
        }
    }""",
        [0],
        (182, 218),
    )
    cur_harness = sCP_Harness(
        name="JenkinsTwo",
        src_path=cp_jenkins_path
        / (
            "fuzz/jenkins-harness-two/src/main/java/com/aixcc/jenkins/harnesses/"
            + "two/JenkinsTwo.java"
        ),
        bin_path=None,
    )
    gc = DummyContext(
        no_llm=False,
        language="jvm",
        scp=sCP(
            name="Jenkins",
            proj_path=cp_jenkins_path,
            cp_src_path=cp_jenkins_path / "repo",
            aixcc_path=cp_jenkins_path / ".aixcc",
            built_path=None,
            language="jvm",
            harnesses={
                "JenkinsTwo": cur_harness,
            },
        ),
    )
    lsp_container_url = setup_lsp["aixcc/jvm/jenkins"]
    assert lsp_container_url is not None
    os.environ["LSP_SERVER_URL"] = lsp_container_url

    async with gc.init():
        mcga = MakeCallGraphAgent(
            target_fn=target_fn,
            config=gc,
            cache={},
            priority_queue=asyncio.PriorityQueue(),
        )
        graph = mcga.compile()

        mcga_state = await graph.ainvoke({"messages": []}, gc.graph_config)

        cg_root_node: FuncInfo = mcga_state["cg_root_node"]

        logger.info(f"fn_name: {cg_root_node.name}")
        for child in cg_root_node.children:
            logger.info(f" - {child.name}: {child.model_dump_json(ident=2)}")


@pytest.fixture
def dummy_mcga():
    b = MakeCallGraphAgent.__new__(MakeCallGraphAgent)
    return b


@pytest.fixture
def one_function_multiple_diffs_java():
    with tempfile.TemporaryDirectory() as tmpdir:
        cp_dir = Path(tmpdir)
        repo_dir = cp_dir / "repo"
        repo_dir.mkdir()

        # Write a Java file with two methods
        java_file = repo_dir / "MyClass.java"
        java_file.write_text(
            r"""public class MyClass {
    public void methodA() {
        System.out.println("Hello A");
    }

    public void methodB() {
        System.out.println("Hello 1");
        System.out.println("Hello 2");
        System.out.println("Hello 3");
        System.out.println("Hello 4");
        System.out.println("Hello 5");
        System.out.println("Hello 6");
        System.out.println("Hello 7");
        System.out.println("Hello 8");
        System.out.println("Hello 9");
        System.out.println("Hello 10");
    }

    public void dummy1() {
        System.out.println("Hello dummy1");
    }

    public void dummy2() {
        System.out.println("Hello dummy2");
    }

    public void dummy3() {
        System.out.println("Hello dummy3");
    }

    public void dummy4() {
        System.out.println("Hello dummy4");
    }
}
"""
        )

        # Create a diff that only changes methodA
        diff_text = r"""\
diff --git a/MyClass.java b/MyClass.java
index e69de29..abcdef1 100644
--- a/MyClass.java
+++ b/MyClass.java
@@ -2,3 +2,3 @@
     public void methodA() {
-        System.out.println("Hello A previous");
+        System.out.println("Hello A");
     }
@@ -6,3 +6,3 @@
     public void methodB() {
-        System.out.println("Hello 1 previous");
+        System.out.println("Hello 1);
         System.out.println("Hello 2");
@@ -11,3 +11,3 @@
         System.out.println("Hello 5");
-        System.out.println("Hello 6 previous");
+        System.out.println("Hello 6");
         System.out.println("Hello 7");
"""
        diff_path = cp_dir / "java_method.diff"
        diff_path.write_text(diff_text)
        yield diff_path, repo_dir


@pytest.mark.skip(reason="LSP on an arbitrary project is not available")
@pytest.mark.asyncio
async def test_update_interest_info_java(dummy_mcga, one_function_multiple_diffs_java):
    diff_path, repo_dir = one_function_multiple_diffs_java

    context = MagicMock(spec=GlobalContext)
    context._cp = MagicMock(spec=CP)
    context._cp.diff_path = diff_path
    context._cp.cp_src_path = repo_dir
    context._init_diff = lambda: GlobalContext._init_diff(context)

    lsp_config = MultilspyConfig.from_dict(
        {
            "code_language": "java",
        }
    )
    msp_logger = MultilspyLogger()

    lsp = LanguageServer.create(lsp_config, msp_logger, repo_dir.as_posix())
    async with lsp.start_server():
        context.lsp_server = lsp
        await context._init_diff()

        dummy_mcga.gc = context

    print(context.function_diffs)
    assert 0
    method_a = FuncInfo(
        func_location=LocationInfo(
            file_path=str(repo_dir / "MyClass.java"),
            func_name="methodA",
            start_line=2,
            end_line=4,
        ),
        func_body="_dummy_body_",
    )
    dummy_mcga._update_interest_info(method_a, {})

    assert method_a.interest_info
    assert method_a.interest_info.is_interesting and method_a.interest_info.diff
    assert "dummy4" not in method_a.interest_info.diff
    assert "Hello A previous" in method_a.interest_info.diff

    # Multi-hunk patch
    method_b = FuncInfo(
        func_location=LocationInfo(
            file_path=str(repo_dir / "MyClass.java"),
            func_name="methodB",
            start_line=6,
            end_line=17,
        ),
        func_body="_dummy_body_",
    )
    dummy_mcga._update_interest_info(method_b, {})

    assert method_b.interest_info
    assert method_b.interest_info.is_interesting and method_b.interest_info.diff
    assert "dummy4" not in method_b.interest_info.diff
    assert "Hello 1 previous" in method_b.interest_info.diff
    assert "Hello 6 previous" in method_b.interest_info.diff


@pytest.mark.skip(reason="LSP should be set up")
@pytest.mark.asyncio
async def test_update_interest_info_java_wo_lsp(
    dummy_mcga, one_function_multiple_diffs_java
):
    diff_path, repo_dir = one_function_multiple_diffs_java

    context = MagicMock(spec=GlobalContext)
    context._cp = MagicMock(spec=CP)
    context._cp.diff_path = diff_path
    context._cp.cp_src_path = repo_dir
    context._init_diff = lambda: GlobalContext._init_diff(context)

    lsp = None
    context.lsp_server = lsp
    await context._init_diff()

    dummy_mcga.gc = context

    method_a = FuncInfo(
        func_location=LocationInfo(
            file_path=str(repo_dir / "MyClass.java"),
            func_name="methodA",
            start_line=2,
            end_line=4,
        ),
        func_body="_dummy_body_",
    )
    dummy_mcga._update_interest_info(method_a, {})

    assert method_a.interest_info
    assert method_a.interest_info.is_interesting and method_a.interest_info.diff
    assert "dummy4" not in method_a.interest_info.diff
    assert "Hello A previous" in method_a.interest_info.diff

    # Multi-hunk patch
    method_b = FuncInfo(
        func_location=LocationInfo(
            file_path=str(repo_dir / "MyClass.java"),
            func_name="methodB",
            start_line=6,
            end_line=17,
        ),
        func_body="_dummy_body_",
    )
    dummy_mcga._update_interest_info(method_b, {})

    assert method_b.interest_info
    assert method_b.interest_info.is_interesting and method_b.interest_info.diff
    assert "dummy4" not in method_b.interest_info.diff
    assert "Hello 1 previous" in method_b.interest_info.diff
    assert "Hello 6 previous" in method_b.interest_info.diff


def test_tainted_args():
    from mlla.agents.mcga import extract_args

    callee_name = "MEFLib.doImport"
    callsite_code = """
                 List<String> ids = MEFLib.doImport(
                    fileType,
                    uuidAction,
                    style,
                    params.getUuid(),
                    isTemplate,
                    Iterables.toArray(params.getCategories(), String.class),
                    params.getOwnerIdGroup(),
                    params.getValidate() != NOVALIDATION,
                    false, context, file);
    """

    args = extract_args(callsite_code, callee_name)
    logger.info(f"args: {args}")
    assert len(args) == 11


def test_zookeeper_extract_args():
    from mlla.agents.mcga import extract_args

    callee_name = "createNode"
    callsite_code = """
                     rc.path = createTxn.getPath();
                createNode(
                    createTxn.getPath(),
                    createTxn.getData(),
                    createTxn.getAcl(),
                    createTxn.getEphemeral() ? header.getClientId() : 0,
                    createTxn.getParentCVersion(),
                    header.getZxid(),
                    header.getTime(),
                    null);
                break;
    """

    args = extract_args(callsite_code, callee_name)
    logger.info(f"args: {args}")
    assert len(args) == 8


def test_extract_args2():
    from mlla.agents.mcga import extract_args

    callee_name = "rewind"
    callsite_code = """
         private ByteBuffer readRecord() throws IOException {
        recordBuffer.rewind();
        final int readNow = archive.read(recordBuffer);
    """

    args = extract_args(callsite_code, callee_name)
    logger.info(f"args: {args}")
    assert len(args) == 0


def test_extract_args3():
    from mlla.agents.mcga import extract_args

    callee_name = "intckPrepareFmt"
    callsite_code = """
     pStmt = intckPrepareFmt(p,
       /* Table tabname contains a single row. The first column, "db", contains
       ** the name of the db containing the table (e.g. "main") and the second,
       ** "tab", the name of the table itself.  */
       "WITH tabname(db, tab, idx, prev) AS (SELECT %Q, %Q, NULL, %Q)"
       ""
       "%s" /* zCommon */

       /* expr(e) contains one row for each index on table zObj. Value e
       ** is set to an expression that evaluates to NULL if the required
       ** entry is present in the index, or an error message otherwise.  */
       ", expr(e, p) AS ("
       "  SELECT format('CASE WHEN EXISTS \n"
       "    (SELECT 1 FROM %%Q.%%Q AS i INDEXED BY %%Q WHERE %%s%%s)\n"
       "    THEN NULL\n"
       "    ELSE format(''entry (%%s,%%s) missing from index %%s'', %%s, %%s)\n"
       "  END\n'"
       "    , t.db, t.tab, i.name, i.match_expr, ' AND (' || partial || ')',"
       "      i.idx_ps, t.ps_pk, i.name, i.idx_idx, t.pk_pk),"
       "    CASE WHEN partial IS NULL THEN NULL ELSE i.partial_alias END"
       "  FROM tabpk t, idx i"
       ")"

       ", numbered(ii, cond, e) AS ("
       "  SELECT 0, 'n.ii=0', 'NULL'"
       "    UNION ALL "
       "  SELECT row_number() OVER (),"
       "      '(n.ii='||row_number() OVER ()||COALESCE(' AND '||p||')', ')'), e"
       "  FROM expr"
       ")"

       ", counter_with(w) AS ("
       "    SELECT 'WITH intck_counter(ii) AS (\n  ' || "
       "       group_concat('SELECT '||ii, ' UNION ALL\n  ') "
       "    || '\n)' FROM numbered"
       ")"
       ""
       ", case_statement(c) AS ("
       "    SELECT 'CASE ' || "
       "    group_concat(format('\n  WHEN %%s THEN (%%s)', cond, e), '') ||"
       "    '\nEND AS error_message'"
       "    FROM numbered"
       ")"
       ""

       /* This table contains a single row consisting of a single value -
       ** the text of an SQL expression that may be used by the main SQL
       ** statement to output an SQL literal that can be used to resume
       ** the scan if it is suspended. e.g. for a rowid table, an expression
       ** like:
       **
       **     format('(%d,%d)', _rowid_, n.ii)
       */
       ", thiskey(k, n) AS ("
       "    SELECT o_pk || ', ii', n_pk+1 FROM tabpk"
       ")"
       ""
       ", whereclause(w_c) AS ("
       "    SELECT CASE WHEN prev!='' THEN "
       "    '\nWHERE (' || o_pk ||', n.ii) > ' || prev"
       "    ELSE ''"
       "    END"
       "    FROM tabpk, tabname"
       ")"
       ""
       ", main_select(m, n) AS ("
       "  SELECT format("
       "      '%%s, %%s\nSELECT %%s,\n%%s\nFROM intck_wrapper AS o"
                ", intck_counter AS n%%s\nORDER BY %%s', "
       "      w, ww.s, c, thiskey.k, whereclause.w_c, t.o_pk"
       "  ), thiskey.n"
       "  FROM case_statement, tabpk t, counter_with, "
       "       wrapper_with ww, thiskey, whereclause"
       ")"

       "SELECT m, n FROM main_select",
       p->zDb, zObj, zPrev, zCommon
     );
    """

    args = extract_args(callsite_code, callee_name)
    logger.info(f"args: {args}")
    assert len(args) == 6


def test_extract_args4():
    from mlla.agents.mcga import extract_args

    callee_name = "MediaType"
    callsite_code = """
    if (matcher.matches()) {
            return new MediaType(matcher.group(2), matcher.group(3),
                    parseParameters(matcher.group(1)))
    """

    args = extract_args(callsite_code, callee_name)
    logger.info(f"args: {args}")
    assert len(args) == 3


def test_extract_args5():
    from mlla.agents.mcga import extract_args

    callee_name = "TikaConfigException"
    callsite_code = """
                            throw new TikaConfigException(
                                "Class " + name + " is not of type: " + iface);
                    }
    """

    args = extract_args(callsite_code, callee_name)
    logger.info(f"args: {args}")
    assert len(args) == 1


@pytest.mark.asyncio
async def test_mcga_invalid_sink(config: GlobalContext):
    fn_name = "target_debug_printf_nofunc"
    fn_path = "/src/repo/gdb/target.c"
    fn_body = r"""#define target_debug_printf_nofunc(fmt, ...) \
  debug_prefixed_printf_cond_nofunc (targetdebug > 0, "target", fmt, ##__VA_ARGS__)"""
    fn_loc = (122, 123)
    target_fn: tuple[str, str, str, list[int], tuple[int, int]] = (
        fn_name,
        fn_path,
        fn_body,
        [],
        fn_loc,
    )
    agent = MakeCallGraphAgent(
        target_fn, config=config, cache={}, priority_queue=asyncio.PriorityQueue()
    )

    mock_report = MCGASinkDetectReport(
        callsites=[
            CalleeRes(
                name="debug_prefixed_printf_cond_nofunc",
                needs_to_analyze=True,
                tainted_args=[],
                line_range=((123, 3), (123, 75)),
            )
        ],
        sanitizer_candidates=["BufferUnderflow"],
        is_vulnerable=True,
        sink_line_number=100,
        sink_line="new_line = format[strlen (format) - 1] == '\\n';",
        sink_analysis_message=(
            "The macro target_debug_printf_nofunc passes the user-supplied fmt directly"
            " through debug_prefixed_printf_cond_nofunc into debug_vprintf. In"
            " debug_vprintf, after printing, the code checks new_line by indexing"
            " format[strlen(format) - 1] without verifying that strlen(format) > 0. If"
            " fmt is the empty string, strlen(fmt) is 0, and format[-1] is accessed,"
            " causing a buffer underflow."
        ),
    )

    line_before_fn_body = mock_report
    with pytest.raises(ExpectedException) as e:
        agent._verify_valid_sink_line(line_before_fn_body)
        assert "Invalid sink line number:" in str(e.value)

    line_after_fn_body = mock_report
    line_after_fn_body.sink_line_number = 200
    with pytest.raises(ExpectedException) as e:
        agent._verify_valid_sink_line(line_after_fn_body)
        assert "Invalid sink line number:" in str(e.value)

    invalid_sink_line = mock_report
    invalid_sink_line.sink_line_number = 123
    with pytest.raises(ExpectedException) as e:
        agent._verify_valid_sink_line(invalid_sink_line)
        assert "Invalid sink line:" in str(e.value)

    correct_sink_line = mock_report
    correct_sink_line.sink_line_number = 123
    correct_sink_line.sink_line = "debug_prefixed_printf_cond_nofunc"
    agent._verify_valid_sink_line(correct_sink_line)


@pytest.mark.asyncio
# @pytest.mark.xfail(reason="Infinite loop is not handled yet")
async def test_mcga_infinite_invalid_answer(tmp_path, mock_c_cp):
    config = DummyContext(no_llm=False, language="c", scp=mock_c_cp)
    fn_name = "target_debug_printf_nofunc"
    fn_path = tmp_path / "target.c"
    fn_body = r"""#define target_debug_printf_nofunc(fmt, ...) \
  debug_prefixed_printf_cond_nofunc (targetdebug > 0, "target", fmt, ##__VA_ARGS__)"""
    fn_loc = (1, 2)

    fn_path.write_text(fn_body)

    target_fn: tuple[str, str, str, list[int], tuple[int, int]] = (
        fn_name,
        fn_path.as_posix(),
        fn_body,
        [],
        fn_loc,
    )

    func_info = FuncInfo(
        func_location=LocationInfo(
            file_path=fn_path.as_posix(),
            func_name=fn_name,
            start_line=fn_loc[0],
            end_line=fn_loc[1],
        ),
        func_body=fn_body,
    )
    config.function_diffs = {}
    config.cpua_target_fns = []
    config.candidate_queue = None
    config.code_indexer = None

    mock_report = MCGASinkDetectReport(
        callsites=[
            # CalleeRes(
            #     name="debug_prefixed_printf_cond_nofunc",
            #     needs_to_analyze=True,
            #     tainted_args=[],
            #     line_range=((123, 3), (123, 75)),
            # )
        ],
        sanitizer_candidates=["BufferUnderflow"],
        is_vulnerable=True,
        sink_line_number=100,
        sink_line="new_line = format[strlen (format) - 1] == '\\n';",
        sink_analysis_message=(
            "The macro target_debug_printf_nofunc passes the user-supplied fmt directly"
            " through debug_prefixed_printf_cond_nofunc into debug_vprintf. In"
        ),
    )

    mock_messages = [SystemMessage("Invalid message"), HumanMessage("Invalid message")]
    call_count = 0

    # Store the original method before patching
    original_code_understand_step1 = MakeCallGraphAgent.code_understand_step1

    async def fake_code_understand_step1(state):
        nonlocal call_count
        call_count += 1
        if call_count == 10:
            raise Exception("Infinite loop raised by test")
        # call original code_understand_step1
        return await original_code_understand_step1(agent, state)

    def fake_prepare_step1_messages():
        return MakeCallGraphOverallState(
            messages=mock_messages,
            step=1,
            resolved={"step1": False, "step2": False},
            done=False,
            from_cache=False,
            cg_root_node=None,
        )

    async def fake_tool_model(state, tools):
        return MakeCallGraphOverallState(
            messages=state["messages"] + [AIMessage(mock_report.model_dump_json())],
            step=1,
            resolved={"step1": False, "step2": False},
            done=False,
            from_cache=False,
            cg_root_node=None,
        )

    from mlla.agents import mcga

    with patch.object(
        MakeCallGraphAgent, "code_understand_step1", new_callable=AsyncMock
    ) as mock_code_understand_step1, patch.object(
        MakeCallGraphAgent, TOOL_MODEL, new_callable=AsyncMock
    ) as mock_tool_model, patch.object(
        MakeCallGraphAgent, "_prepare_step1_messages", new_callable=Mock
    ) as mock_prepare_step1_messages, patch.object(
        mcga, "get_current_cgs_from_redis", new_callable=AsyncMock
    ):

        mock_code_understand_step1.side_effect = fake_code_understand_step1
        mock_tool_model.side_effect = fake_tool_model
        mock_prepare_step1_messages.side_effect = fake_prepare_step1_messages

        agent = MakeCallGraphAgent(
            target_fn, config=config, cache={}, priority_queue=asyncio.PriorityQueue()
        )
        with patch.object(agent, "current_fn_info", new=func_info), patch.object(
            agent, "_callees_from_parser", new=[("a", 1, 1)]
        ):
            _mcga = agent.compile()
            async for state in _mcga.astream({"messages": []}, config.graph_config):
                for k, v in state.items():
                    if v and "messages" in v:
                        logger.info(f"{k}:")
                        for m in v["messages"]:
                            logger.info(f"- {m}")
                    elif v:
                        logger.info(f"{k}: {v}")
