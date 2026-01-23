import os

import pytest
from loguru import logger

from mlla.agents.cpua import CPUnderstandAgent
from mlla.codeindexer.codeindexer import CodeIndexer
from mlla.utils.cgparser import validate_functions
from mlla.utils.cp import sCP, sCP_Harness
from tests.dummy_context import DummyContext


@pytest.fixture
def cpua():
    """
    CPUnderstandAgent.__init__ sets up many dependencies internally,
    so for testing purposes, we temporarily patch __init__
    to only use the deserialize method.
    """
    original_init = CPUnderstandAgent.__init__
    # skip __init__ for testing
    CPUnderstandAgent.__init__ = lambda self, config: None
    agent_instance = CPUnderstandAgent(DummyContext(no_llm=False, language="jvm"))
    # restore __init__
    CPUnderstandAgent.__init__ = original_init
    return agent_instance


@pytest.mark.parametrize("setup_lsp", [["aixcc/jvm/jenkins"]], indirect=True)
@pytest.mark.asyncio
@pytest.mark.skip(reason="This test is too slow.")
async def test_validate_functions(
    setup_lsp: dict, cp_jenkins_path, code_indexer: CodeIndexer
):

    fn_lst = [
        "fuzzerTestOneInput",
        "fuzz",
        "setup_utilmain",
        "setup_replacer",
        "doexecCommandUtils",
    ]
    cur_harness = sCP_Harness(
        name="JenkinsTwo",
        src_path=cp_jenkins_path
        / (
            "fuzz/jenkins-harness-two/src/main/java/com/aixcc/jenkins/harnesses/"
            + "two/JenkinsTwo.java"
        ),
        bin_path=None,
    )
    cp_src_path = cp_jenkins_path / "repo"
    gc = DummyContext(
        no_llm=False,
        language="jvm",
        scp=sCP(
            name="Jenkins",
            proj_path=cp_jenkins_path,
            cp_src_path=cp_src_path,
            aixcc_path=cp_jenkins_path / ".aixcc",
            built_path=None,
            language="jvm",
            harnesses={
                "JenkinsTwo": cur_harness,
            },
        ),
    )
    await code_indexer.index_project(
        "test_validate_functions",
        [cur_harness.src_path.parent],
        "jvm",
    )
    logger.info(f"gc.lsp_server: {gc.lsp_server}")
    gc.code_indexer = code_indexer

    lsp_container_url = setup_lsp["aixcc/jvm/jenkins"]
    logger.info(f"lsp_container_url: {lsp_container_url}")
    assert lsp_container_url is not None

    os.environ["LSP_SERVER_URL"] = lsp_container_url

    # server_cm = gc.lsp_server.start_server()
    # await asyncio.create_task(server_cm.__aenter__())
    async with gc.lsp_server.start_server():
        last_positions, emsgs = await validate_functions(
            fn_lst,
            cur_harness.src_path.as_posix(),
            "setup_utilmain",
            gc,
            cp_src_path.as_posix(),
        )

        assert len(emsgs) == 2
        logger.info(f"emsgs: {emsgs}")

    # if server_cm:
    #     await server_cm.__aexit__(None, None, None)
