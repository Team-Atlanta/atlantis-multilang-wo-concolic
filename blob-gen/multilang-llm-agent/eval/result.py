from pathlib import Path
from typing import Dict, List, Set, Tuple

from pydantic import BaseModel, Field

from eval.cpv_result import CPVResult, get_cpv_res
from eval.harness import collect_harnesses_in_result, parse_target_harness
from eval.mlla_result import MLLAResult
from eval.utils import get_latest_file, load_yaml_file, logger
from mlla.agents.bcda_experimental import deserialize_bcda
from mlla.agents.cpua import deserialize_cpua
from mlla.utils.ci_parse import BlobInfo, HarnessStatus


# Type definitions for better code clarity
class ModelMetrics(BaseModel):
    total_cost: float = Field(default=0)
    execution_time: float = Field(default=0)
    llm_calls: int = Field(default=0)
    llm_tokens: int = Field(default=0)


CPVResults = Dict[Tuple[str, str], Dict[str, CPVResult]]


class ModelResult(BaseModel):
    metrics: ModelMetrics = Field(default_factory=ModelMetrics)  # Overall metrics
    harness_status: Dict[Tuple[str, str], HarnessStatus] = Field(
        default_factory=dict
    )  # For exploit status
    sanitizer_results: Dict[Tuple[str, str], List[tuple[str, BlobInfo]]] = Field(
        default_factory=dict
    )  # For sanitizer information
    detailed_metrics: Dict[Tuple[str, str], ModelMetrics] = Field(
        default_factory=dict
    )  # Per target-harness metrics
    cpv_results: CPVResults = Field(default_factory=dict)  # Per agent metrics

    def initalize_harness_metrics(self, target: str, harnesses: Set[str]):
        for harness in harnesses:
            key = (target, harness)
            self.harness_status[key] = HarnessStatus(
                exploited=False, successful_blobs=0, total_blobs=0
            )
            self.sanitizer_results[key] = []
            self.detailed_metrics[key] = ModelMetrics()
            self.cpv_results[key] = {}

    def process_harness_status(self, mlla_result: MLLAResult, target: str):
        for harness, status in mlla_result.harness_status.items():
            key = (target, str(harness))
            self.harness_status[key] = HarnessStatus(
                exploited=status.exploited,
                successful_blobs=status.successful_blobs,
                total_blobs=status.total_blobs,
            )

    def process_sanitizer_results(self, mlla_result: MLLAResult, target: str):
        """Process sanitizer results data."""
        sanitizer_names = set()
        for sanitizer_name, res in mlla_result.sanitizer_results.items():
            for result in res:
                h_name = result.harness
                key = (target, str(h_name))
                if sanitizer_name not in sanitizer_names:
                    sanitizer_names.add(sanitizer_name)
                    self.sanitizer_results[key].append(
                        (
                            sanitizer_name,
                            BlobInfo(
                                blob=result.blob,
                                harness=h_name,
                            ),
                        )
                    )

    def process_metrics(
        self, mlla_result: MLLAResult, target: str, harnesses: Set[str]
    ) -> None:
        """Process metrics data."""
        total = mlla_result.llm_metrics.total
        self.metrics.total_cost += total.total_cost
        minutes = total.execution_time / 60.0
        self.metrics.execution_time += minutes
        self.metrics.llm_tokens += total.total_tokens
        self.metrics.llm_calls += total.successful_requests

        for harness in harnesses:
            key = (target, harness)
            self.detailed_metrics[key].total_cost += total.total_cost
            self.detailed_metrics[key].execution_time += total.execution_time
            self.detailed_metrics[key].llm_tokens += total.total_tokens
            self.detailed_metrics[key].llm_calls += total.successful_requests

    def process_mlla_result(
        self,
        mlla_result: MLLAResult,
        target_name: str,
        results_dir: Path,
        crash_logs: dict[tuple[str, str], dict[str, Path]],
    ) -> None:
        """Process data from a single result file."""
        harnesses = collect_harnesses_in_result(mlla_result)
        self.initalize_harness_metrics(target_name, harnesses)
        self.process_harness_status(mlla_result, target_name)
        self.process_sanitizer_results(mlla_result, target_name)
        self.process_metrics(mlla_result, target_name, harnesses)
        cpv_results = process_cpv_results(
            target_name,
            harnesses,
            results_dir,
            crash_logs,
        )
        self.cpv_results.update(cpv_results)


# Type alias for detailed metrics
DetailedMetrics = Dict[Tuple[str, str], ModelMetrics]


def group_result_files(
    results_dir: Path,
) -> Dict[Path, List[Path]]:  # (target_dir, [result_files])
    """Group result files by target directory."""
    target_results: Dict[Path, List[Path]] = {}
    for result_file in sorted(results_dir.rglob("mlla-result-*.yaml"), reverse=True):
        target_dir = result_file.parent
        if target_dir not in target_results:
            target_results[target_dir] = []
        target_results[target_dir].append(result_file)
    return target_results


def process_cpv_results(
    target: str,
    harnesses: Set[str],
    results_dir: Path,
    crash_logs: dict[tuple[str, str], dict[str, Path]],
) -> CPVResults:
    """Process CPV results from a target directory."""
    cpua_dir = results_dir / "cpua"
    bcda_dir = results_dir / "bcda"
    cpua_state = None
    bcda_state = None

    if cpua_dir.exists():
        latest_cpua_file = get_latest_file(list(cpua_dir.glob("*.json*")))
        cpua_content = latest_cpua_file.read_text(encoding="utf-8")
        cpua_state = deserialize_cpua(cpua_content)

    if bcda_dir.exists():
        latest_bcda_file = get_latest_file(list(bcda_dir.glob("*.json*")))
        bcda_content = latest_bcda_file.read_text(encoding="utf-8")
        bcda_state = deserialize_bcda(None, bcda_content)

    res = {}

    for harness in harnesses:
        key = (target, harness)
        logger.info(f"crash_logs: {crash_logs.get(key)}")
        crash_log_dict = crash_logs.get(key, {})

        cpv_res_dict = {}

        for cpv_id, log_path in crash_log_dict.items():
            cpv_res = get_cpv_res(cpv_id, log_path, cpua_state, bcda_state, harness)
            if cpv_res is not None:
                cpv_res_dict[cpv_id] = cpv_res

        res[key] = cpv_res_dict

    return res


def get_model_results(
    results_dir: Path,
    crash_logs: dict[tuple[str, str], dict[str, Path]],
) -> ModelResult:
    """Get results from a model's result directory."""
    results = ModelResult()
    target_results: Dict[Path, List[Path]] = group_result_files(
        results_dir
    )  # (target_dir, [result_files])

    if not target_results:
        return results

    for target_dir, files in target_results.items():
        try:
            latest_file = get_latest_file(files)
            latest_result_data = load_yaml_file(latest_file)
            target_name, _ = parse_target_harness(
                latest_file, results_dir
            )  # (target, harness)
            logger.info(f"Loading: {latest_file}")

            mlla_result = MLLAResult.model_validate(latest_result_data)
            logger.info(f"MLLAResult Loaded: {latest_result_data}")
            results.process_mlla_result(
                mlla_result, target_name, latest_file.parent, crash_logs
            )
            logger.info(f"MLLAResult Processed: {latest_file.parent}")

        except Exception as e:
            import traceback

            logger.warning(f"Error reading {files[0]}: {e}")
            print(traceback.format_exc())

    return results
