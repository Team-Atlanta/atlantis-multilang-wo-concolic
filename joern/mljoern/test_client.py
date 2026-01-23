from client import JoernClient
from loguru import logger
import os

if __name__ == "__main__":
    url = f"http://localhost:9909/query-sync"
    os.environ["JOERN_URL"] = url
    client = JoernClient()
    res, valid = client.query("cpg.method.size")
    logger.info(res)
