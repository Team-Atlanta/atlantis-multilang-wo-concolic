################################################################################
### This is for archieving files for CP build
################################################################################

FROM ghcr.io/aixcc-finals/base-builder-jvm:v1.3.0
COPY --from=multilang-builder-jvm /usr/local/bin/jazzer_agent_deploy.jar /multilang-builder/jazzer_agent_deploy.jar
COPY --from=multilang-builder-jvm /usr/local/bin/jazzer_driver /multilang-builder/jazzer_driver
COPY --from=multilang-builder-jvm /usr/local/lib/jazzer_api_deploy.jar /multilang-builder/jazzer_api_deploy.jar
COPY --from=multilang-builder-jvm /usr/local/bin/jazzer_junit.jar /multilang-builder/jazzer_junit.jar
