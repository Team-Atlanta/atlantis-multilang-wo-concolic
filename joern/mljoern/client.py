from loguru import logger
import requests
import json
import os
import re
from typing import Optional

class JoernClient:
    def __init__(self, url: Optional[str] = None, timeout: int = 3600):
        self.__timeout = timeout
        if not url:
            url = os.getenv("JOERN_URL")
            if url is None:
                raise ValueError("JOERN_URL is not set")
        self.__url = "http://" + url
    
    def restart(self, force: bool = False):
        try:
            res = requests.get(self.__url + "/restart", headers={"force": "true" if force else "false"})
            if res.status_code == 200:
                return (res.json(), True)
        except requests.Timeout:
            logger.warning(f"Joern server restart timeout")
            return ({}, False)
        except Exception as e:
            logger.warning(f"Joern server restart error: {e}")

        return ({}, True)
    
    def check_health(self):
        try:
            res = requests.get(self.__url + "/check-health")
            if res.status_code == 200:
                return (res.json(), True)
        except requests.Timeout:
            logger.warning(f"Joern server check health timeout")
            return ({}, False)
        except Exception as e:
            logger.warning(f"Joern server check health error: {e}")
        return ({}, True)
    
    def query_colored(self, script, timeout=-1) -> tuple[dict, bool]:
        data = {"query": script}
        timeout = timeout if timeout > 0 else self.__timeout if timeout < 0 else None
        try:
            res = requests.post(self.__url + "/query-sync", json=data, timeout=timeout)
            if res.status_code == 200:
                return (res.json(), True)
        except requests.Timeout:
            logger.warning(f" - Joern server query timeout({timeout})")
            return ({}, False)
        except requests.exceptions.RequestException as e:
            logger.debug(f" - Joern server query RequestException: {e}")

        return ({}, True)

    def query(self, script, timeout=-1) -> tuple[dict, bool]:
        res, valid = self.query_colored(script, timeout)
        stdout = res.get("stdout", "")
        ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
        cleaned = ansi_escape.sub('', stdout)
        res["stdout"] = cleaned
        return (res, valid)

    def query_json(self, script, timeout=-1) -> dict:
        res, valid = self.query(script, timeout)

        if not valid:
            return dict()

        stdout = res.get("stdout", "")
        raw_result = stdout[stdout.find("= ") + 1 :]
        if raw_result.startswith('"""'):
            raw_result = "r" + raw_result

        try:
            parsed = json.loads(eval(raw_result))
        except Exception as e:
            logger.warning(f" - Joern server query json error: {e}")
            parsed = dict()

        return parsed

    def _check_joern(self) -> bool:
        try:
            res, valid = self.query("cpg.method.size")

            if not valid:
                return False

            if not (isinstance(res, dict) and res.get("success") is True):
                return False

            stdout = res.get("stdout")
            if not stdout:
                logger.warning(f"Joern check query missing stdout: {res}")
                return False

            size_str = stdout.split("= ")[-1].strip()
            method_size = int(size_str)

            logger.debug(f"Joern check found method size: {method_size}")
            is_ok = method_size >= 1
            if not is_ok:
                logger.warning(f"Joern check found method size < 1: {method_size}")
            return is_ok

        except (ValueError, IndexError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse joern check result: {e}. Response: {res}")
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred during joern check: {e}")
            return False