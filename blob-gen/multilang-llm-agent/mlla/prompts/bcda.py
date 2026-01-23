"""Prompts for Bug Candidate Detection Agent (BCDA)."""

# Vulnerability Classification Prompts
CLASSIFY_SYSTEM = """Determine whether any vulnerabilities triggered by \
{sanitizer} sanitizers exist or not.
Vulnerability can exist in the root_node or reachable_node.
If additional code is needed to determine the vulnerability, request the code
information when it is highly necessary.
Required filename must be the exact one to be parsed by parser.

Output format should be: {{"jazzer_triggering_line": the copy of vulnerable
line, "required_info": list filenames required to determine vulnerability like
[filename, filename, ...]}}"""

# Code Format Templates
CODE_FORMAT = """<root_node>
{root_body}
</root_node>
<reachable_node>
{reachable_body}
</reachable_node>"""
