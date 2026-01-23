from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from bs4 import BeautifulSoup

from eval.result import (
    CPVResult,
    HarnessStatus,
    ModelMetrics,
    ModelResult,
    get_model_results,
)
from eval.utils import logger
from mlla.utils.ci_parse import BlobInfo

GroupedData = Dict[
    str,  # cp_name
    Dict[
        str,  # harness_name
        List[Tuple[str, str, ModelMetrics, HarnessStatus | None, CPVResult, int]],
    ],
]


def _choose_cpv_result(
    sanitizer_name: str, cpv_result_list: List[CPVResult], blob_info: BlobInfo
) -> CPVResult:
    # TODO: choose the best cpv result based on the blob info
    # max(cpv_result_list, key=lambda cpv_result: )
    res = cpv_result_list[0]
    res.exploited = True
    return res


def print_cpv_results(cpv_result: CPVResult) -> Tuple[str, str, str]:
    exploited = cpv_result.exploited
    cpua, bcda, bga = "-", "-", "-"

    if exploited:
        bga = "Yes"

    if cpv_result.bcda_res.detected:
        if cpv_result.bcda_res.sanitizer_detected:
            bcda = f"Yes ({cpv_result.bcda_res.hit}/{cpv_result.bcda_res.total})"
        else:
            bcda = f"No ({cpv_result.bcda_res.hit}/{cpv_result.bcda_res.total})"

    if cpv_result.cpua_res.detected:
        cpua = (
            "Yes "
            + f"({cpv_result.cpua_res.reached}"
            + f"/{(cpv_result.cpua_res.total)})"
        )
    else:
        cpua = f"{cpv_result.cpua_res.reached}/{cpv_result.cpua_res.total}"

    return cpua, bcda, bga


def _gen_grouped_data(model_result: ModelResult) -> GroupedData:
    grouped_data: GroupedData = {}

    for (cp_name, harness_name), metrics in model_result.detailed_metrics.items():
        if cp_name not in grouped_data:
            grouped_data[cp_name] = {}
        if harness_name not in grouped_data[cp_name]:
            grouped_data[cp_name][harness_name] = []

        sanitizer_results = model_result.sanitizer_results.get(
            (cp_name, harness_name), []
        )
        harness_status = model_result.harness_status.get((cp_name, harness_name), None)

        cpv_results = model_result.cpv_results.get((cp_name, harness_name), {})

        san_name_to_cpv_res_dict = defaultdict(list)
        sanitizer_to_blobinfo_dict = {}

        for cpv_id, cpv_result in cpv_results.items():
            san_name_to_cpv_res_dict[cpv_result.sanitizer_name].append(cpv_result)

        for sanitizer_name, blobinfo in sanitizer_results:
            sanitizer_to_blobinfo_dict[sanitizer_name] = blobinfo

        expected_sanitizers = set(
            cpv_result.sanitizer_name for cpv_result in cpv_results.values()
        )
        uc_result = 0
        for real_sanitizer_name in sanitizer_to_blobinfo_dict:
            if real_sanitizer_name not in expected_sanitizers:
                uc_result += 1

        for cpv_id, cpv_result in cpv_results.items():
            sanitizer_name = cpv_result.sanitizer_name
            if sanitizer_to_blobinfo_dict:
                blob_info = sanitizer_to_blobinfo_dict.get(sanitizer_name, None)
                if blob_info:
                    cpv_result_list = san_name_to_cpv_res_dict[sanitizer_name]
                    cpv_result = _choose_cpv_result(
                        sanitizer_name, cpv_result_list, blob_info
                    )
            grouped_data[cp_name][harness_name].append(
                (cpv_id, sanitizer_name, metrics, harness_status, cpv_result, uc_result)
            )
    return grouped_data


def result_to_html(model_result: ModelResult) -> str:
    html = "<table border='1' style='border-collapse:collapse;'>"

    # Table Header
    html += """
    <tr>
        <th>cp_name</th>
        <th>harness_name</th>
        <th>cpv_id</th>
        <th>Sanitizer</th>
        <th>CPUA</th>
        <th>BCDA</th>
        <th>BGA</th>
        <th>UC</th>
        <th># of Tokens</th>
        <th># of LLM Calls</th>
        <th>LLM Cost</th>
        <th># of blobs</th>
        <th>succeed</th>
        <th>failed</th>
        <th>running-time</th>
    </tr>"""

    # Generate rows grouped by cp_name and harness_name
    grouped_data: GroupedData = _gen_grouped_data(model_result)

    # Generate table rows
    total_tokens = 0
    total_calls = 0
    total_cost: float = 0.0
    total_blobs = 0
    total_succeeded = 0
    total_failed = 0
    total_time: float = 0.0
    total_cpua = 0
    total_bcda = 0
    total_bga = 0
    total_uc = 0
    total_cp_names = 0
    total_harness_names = 0
    total_sanitizers = 0

    for cp_name, harness_dict in reversed(grouped_data.items()):
        cp_total_rows = sum(len(sanitizers) for sanitizers in harness_dict.values())
        total_cp_names += 1
        cp_first = True

        for harness_name, sanitizer_rows in harness_dict.items():
            harness_first = True
            uc_first = True
            harness_rowspan = len(sanitizer_rows)
            total_harness_names += 1
            sanitizer_rows.sort(key=lambda x: int(x[0].split("_")[1]))

            for idx, (
                cpv_id,
                sanitizer,
                metrics,
                harness_status,
                cpv_result,
                uc_result,
            ) in enumerate(sanitizer_rows):
                total_sanitizers += 1
                html += "<tr>"

                if cp_first:
                    html += f"<td rowspan='{cp_total_rows}'>{cp_name}</td>"
                    cp_first = False

                if harness_first:
                    html += f"<td rowspan='{harness_rowspan}'>{harness_name}</td>"
                    harness_first = False

                cpua, bcd, bga = print_cpv_results(cpv_result)
                if "Yes" in cpua:
                    total_cpua += 1
                if "Yes" in bcd:
                    total_bcda += 1
                if "Yes" in bga:
                    total_bga += 1

                html += f"<td>{cpv_id}</td>"
                html += f"<td>{sanitizer}</td>"
                html += f"<td>{cpua}</td>"
                html += f"<td>{bcd}</td>"
                html += f"<td>{bga}</td>"
                if uc_first:
                    html += f"<td rowspan='{harness_rowspan}'>{uc_result}</td>"
                    uc_first = False
                # Add metrics with rowspan only for first sanitizer row
                if idx == 0:
                    html += f"<td rowspan='{harness_rowspan}'>{metrics.llm_tokens}</td>"
                    html += f"<td rowspan='{harness_rowspan}'>{metrics.llm_calls}</td>"
                    html += (
                        f"<td rowspan='{harness_rowspan}'>{metrics.total_cost:.5f}</td>"
                    )

                    blobs = harness_status.total_blobs if harness_status else 0
                    succeeded = harness_status.successful_blobs if harness_status else 0
                    failed = (
                        (harness_status.total_blobs - harness_status.successful_blobs)
                        if harness_status
                        else 0
                    )

                    html += f"<td rowspan='{harness_rowspan}'>{blobs}</td>"
                    html += f"<td rowspan='{harness_rowspan}'>{succeeded}</td>"
                    html += f"<td rowspan='{harness_rowspan}'>{failed}</td>"
                    html += (
                        f"<td rowspan='{harness_rowspan}'>"
                        f"{metrics.execution_time:.2f}</td>"
                    )

                    # Update totals
                    total_tokens += metrics.llm_tokens
                    total_calls += metrics.llm_calls
                    total_cost += metrics.total_cost
                    total_blobs += blobs
                    total_uc += uc_result
                    total_succeeded += succeeded
                    total_failed += failed
                    total_time += metrics.execution_time

                html += "</tr>"

    # Add total row
    html += "<tr style='font-weight:bold;'>"
    html += f"<td>{total_cp_names}</td>"
    html += f"<td>{total_harness_names}</td>"
    html += f"<td>{total_sanitizers}</td>"
    html += f"<td>{total_sanitizers}</td>"
    html += f"<td>{total_cpua}</td>"
    html += f"<td>{total_bcda}</td>"
    html += f"<td>{total_bga}</td>"
    html += f"<td>{total_uc}</td>"
    html += f"<td>{total_tokens}</td>"
    html += f"<td>{total_calls}</td>"
    html += f"<td>{total_cost:.5f}</td>"
    html += f"<td>{total_blobs}</td>"
    html += f"<td>{total_succeeded}</td>"
    html += f"<td>{total_failed}</td>"
    html += f"<td>{total_time:.2f}</td>"
    html += "</tr>"

    html += "</table>"

    parsed_html = BeautifulSoup(html, "html.parser")
    pretty_html = parsed_html.prettify()
    return pretty_html


def print_results_summary_new(
    output_dir: str | None = None,
    model_results: List[ModelResult] = [],
) -> None:
    """Print combined summary of results from all models and temperatures."""
    import html2text

    for model_result in model_results:
        html_str = result_to_html(
            model_result,
        )

        print(html_str)

        html_text = html2text.html2text(html_str)

        print(html_text)

        if output_dir is not None:
            with open(output_dir + "/results_summary.html", "w") as f:
                f.write(html_str)
            logger.info(f"Results summary saved to {output_dir}/results_summary.html")


def print_results_summary(
    base_path: str,
    crash_logs: dict[tuple[str, str], dict[str, Path]],
    output_dir: str | None = None,
) -> None:
    """Print combined summary of results from all models and temperatures."""
    logger.info("Printing combined results summary...")

    model_results: List[ModelResult] = []

    # Process each model directory
    path = Path(base_path)

    # Get results for this model+temp
    logger.info(f"Loading data at: {path}")

    results: ModelResult = get_model_results(path, crash_logs)
    if results.harness_status or results.sanitizer_results or results.cpv_results:
        model_results.append(results)

    if not model_results:
        logger.warning("No results found for any models")
        return

    # logger.info(f"Model headers: {model_headers}")
    logger.info(f"Model results: {model_results}")

    print_results_summary_new(
        output_dir=output_dir,
        model_results=model_results,
    )
