import asyncio

from multilspy.multilspy_logger import MultilspyLogger
from multilspy.language_servers.clangd_language.clangd_server import ClangdServer
from multilspy.language_servers.eclipse_jdtls.eclipse_jdtls import EclipseJDTLS

async def main():
    msp_logger = MultilspyLogger()
    ClangdServer.download_server(msp_logger)
    EclipseJDTLS.download_server(msp_logger)

if __name__ == "__main__":
    asyncio.run(main())
