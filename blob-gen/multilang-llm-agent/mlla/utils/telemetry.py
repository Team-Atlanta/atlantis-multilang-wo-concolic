"""Telemetry setup using OpenTelemetry for Phoenix and Traceloop providers."""

import logging
import sys
from typing import Literal

from loguru import logger
from openinference.instrumentation.langchain import LangChainInstrumentor
from phoenix.otel import register

from .cp import get_docker_gateway

# from urllib.parse import urlparse


# from traceloop.sdk import Traceloop


def setup_telemetry(
    project_name: str = "default",
    endpoint: str | None = None,
    provider: Literal["phoenix", "traceloop"] = "phoenix",
) -> None:
    """Setup telemetry with OpenTelemetry.

    This function configures telemetry using a given provider.
    Both providers are set up with OpenTelemetry and LangChain instrumentation.
    Note: RemoveMessage type (beta in LangChain Core) is not yet supported by
    OpenInference and will be skipped in traces.

    Args:
        project_name: Name of the project for tracing
        endpoint: OpenTelemetry collector endpoint
        provider: Telemetry provider to use ("phoenix" or "traceloop")

    Raises:
        SystemExit: If telemetry setup fails
    """
    logger.info(f"Setting up {provider} telemetry for project '{project_name}'")

    try:
        # Set default endpoint if none provided
        if endpoint is None:
            # Always assume we are running inside docker
            endpoint = f"http://{get_docker_gateway()}:6006/v1/traces"
            logger.info(f"Running in Docker, transformed endpoint to: {endpoint}")
            # if is_running_in_docker():
            #     endpoint = f"http://{get_docker_gateway()}:6006/v1/traces"
            #     logger.info(f"Running in Docker, transformed endpoint to: {endpoint}")
            # else:
            #     endpoint = "http://localhost:6006/v1/traces"
            #     logger.info(f"No endpoint provided, using default: {endpoint}")

        if provider == "phoenix":
            _setup_phoenix(project_name, endpoint)
        else:  # traceloop
            raise ValueError("traceloop is deprecated for production")
            # _setup_traceloop(project_name, endpoint)

        logger.info(f"{provider.capitalize()} telemetry setup completed successfully")

    except Exception as e:
        logger.error(f"Failed to setup {provider} telemetry: {e}")
        sys.exit(1)


def _setup_phoenix(project_name: str, endpoint: str) -> None:
    """Setup Phoenix telemetry provider."""
    # Initialize Phoenix tracer
    tracer_provider = register(
        project_name=project_name,
        endpoint=endpoint,
    )

    # Initialize LangChain instrumentation
    logger.info("Setting up OpenInference instrumentation")
    instrumentor = LangChainInstrumentor()
    instrumentor.instrument(tracer_provider=tracer_provider)

    logger.warning(
        "Note: Ignore RemoveMessage error in traces until OpenInference adds support"
    )

    tracer_logger = logging.getLogger("openinference.instrumentation.langchain._tracer")
    logger.info(f"tracer_logger: {tracer_logger}")

    class SuppressExceptionForSpecificMessageFilter(logging.Filter):
        def filter(self, record) -> bool:
            if record.getMessage() == "Failed to get attribute.":
                return False
            return True

    tracer_logger.addFilter(SuppressExceptionForSpecificMessageFilter())
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)


# def _setup_traceloop(project_name: str, endpoint: str) -> None:
#     """Setup Traceloop telemetry provider."""
#     # Initialize Traceloop with OpenLLMetry instrumentation
#     # Extract base URL (scheme + netloc) since Traceloop automatically adds /v1/traces
#     parsed_url = urlparse(endpoint)
#     base_endpoint = f"{parsed_url.scheme}://{parsed_url.netloc}"

#     Traceloop.init(
#         app_name=project_name,
#         api_endpoint=base_endpoint,
#         disable_batch=True,
#         telemetry_enabled=False,  # Disable anonymous usage reporting to Traceloop
#     )
#     logger.info("Setting up OpenLLMetry instrumentation")
