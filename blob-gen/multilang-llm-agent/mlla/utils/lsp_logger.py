# import inspect
import logging

from typing_extensions import TypedDict


class LogLine(TypedDict):
    """
    Represents a line in the Multilspy log
    """

    time: str
    level: str
    caller_file: str
    caller_name: str
    caller_line: int
    message: str


class MLLALspLogger:
    """
    Logger class
    """

    def __init__(self, workdir: str, cp_name: str, harness_name: str = "") -> None:
        self.logger = logging.getLogger("multilspy")
        self.logger.setLevel(logging.INFO)
        self.workdir = workdir
        cp_name = cp_name.replace("/", "_")

        self.log_file_handler = logging.FileHandler(
            f"{self.workdir}/{cp_name}_{harness_name}_lsp.log", mode="w"
        )
        log_formatter = logging.Formatter("%(message)s")
        self.log_file_handler.setFormatter(log_formatter)
        self.log_file_handler.setLevel(logging.INFO)

        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)

        self.logger.addHandler(self.log_file_handler)
        self.logger.propagate = False

    def log(
        self, debug_message: str, level: int, sanitized_error_message: str = ""
    ) -> None:
        """
        Log the debug and santized messages using the logger
        """

        debug_message = debug_message.replace("'", '"').replace("\n", " ")
        sanitized_error_message = sanitized_error_message.replace("'", '"').replace(
            "\n", " "
        )

        # Collect details about the callee
        # curframe = inspect.currentframe()
        # calframe = inspect.getouterframes(curframe, 2)
        # caller_file = calframe[1][1].split("/")[-1]
        # caller_line = calframe[1][2]
        # caller_name = calframe[1][3]

        # Construct the debug log line
        # debug_log_line = LogLine(
        #     time=str(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        #     level=logging.getLevelName(level),
        #     caller_file=caller_file,
        #     caller_name=caller_name,
        #     caller_line=caller_line,
        #     message=debug_message,
        # )

        self.logger.log(
            level=level,
            msg=debug_message,
        )
