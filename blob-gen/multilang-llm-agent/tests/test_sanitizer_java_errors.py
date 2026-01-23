# flake8: noqa: E501
import pytest

from mlla.modules.sanitizer import JazzerSanitizer


def test_detect_fuzzer_security_issue_high_remote_code_execution():
    """Test detection of JazzerSanitizer Remote Code Execution."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueHigh: Remote Code Execution
Unrestricted class/object creation based on externally controlled data may allow
remote code execution depending on available classes on the classpath.
at jaz.Zer.reportFinding(Zer.java:105)
at jaz.Zer.reportFindingIfEnabled(Zer.java:100)
at jaz.Zer.readObject(Zer.java:372)
"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "RemoteCodeExecution"


def test_detect_fuzzer_security_issue_high_xpath_injection():
    """Test detection of JazzerSanitizer XPath Injection."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueHigh: XPath Injection
Injected query: document(2)
at com.code_intelligence.jazzer.sanitizers.XPathInjection.checkXpathExecute(XPathInjection.kt:66)
at jenkins.util.xml.XMLUtils.getValue(XMLUtils.java:236)
at io.jenkins.plugins.toyplugin.Api.doXml(Api.java:137)
at com.aixcc.jenkins.harnesses.three.JenkinsThree.testApi(JenkinsThree.java:262)
at com.aixcc.jenkins.harnesses.three.JenkinsThree.fuzz(JenkinsThree.java:100)
at com.aixcc.jenkins.harnesses.three.JenkinsThree.fuzzerTestOneInput(JenkinsThree.java:76)
Caused by: javax.xml.xpath.XPathExpressionException: javax.xml.transform.TransformerException: Could not find function: document
at java.xml/com.sun.org.apache.xpath.internal.jaxp.XPathImpl.compile(XPathImpl.java:170)
at java.base/java.lang.invoke.MethodHandle.invokeWithArguments(MethodHandle.java:732)
at com.code_intelligence.jazzer.sanitizers.XPathInjection.checkXpathExecute(XPathInjection.kt:56)
... 5 more
Caused by: javax.xml.transform.TransformerException: Could not find function: document
at java.xml/com.sun.org.apache.xpath.internal.compiler.XPathParser.error(XPathParser.java:621)
"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "XPathInjection"


def test_detect_fuzzer_security_issue_low_regex_injection():
    """Test detection of JazzerSanitizer Regular Expression Injection."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueLow: Regular Expression Injection
Regular expression patterns that contain unescaped untrusted input can consume
arbitrary amounts of CPU time. To properly escape the input, wrap it with
Pattern.quote(...).
at com.code_intelligence.jazzer.sanitizers.RegexInjection.hookInternal(RegexInjection.kt:146)
at com.code_intelligence.jazzer.sanitizers.RegexInjection.patternHook(RegexInjection.kt:69)
at io.jenkins.plugins.toyplugin.AccessFilter.doGet(AccessFilter.java:87)
at com.aixcc.jenkins.harnesses.three.JenkinsThree.testAccessFilter(JenkinsThree.java:299)
at com.aixcc.jenkins.harnesses.three.JenkinsThree.fuzz(JenkinsThree.java:103)
at com.aixcc.jenkins.harnesses.three.JenkinsThree.fuzzerTestOneInput(JenkinsThree.java:76)
Caused by: java.util.regex.PatternSyntaxException: Unclosed group near index 7
.*(().*
at java.base/java.util.regex.Pattern.error(Pattern.java:2028)
"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "RegexInjection"


def test_detect_stream_corrupted_exception():
    """Test detection of JazzerSanitizer StreamCorruptedException."""
    output = """== Java Exception: java.io.StreamCorruptedException: invalid stream header: 247B6A6E
at java.base/java.io.ObjectInputStream.readStreamHeader(ObjectInputStream.java:963)
at java.base/java.io.ObjectInputStream.<init>(ObjectInputStream.java:397)
at io.jenkins.plugins.coverage.CompatibleObjectInputStream.<init>(CompatibleObjectInputStream.java:29)
at io.jenkins.plugins.coverage.CoverageProcessor.recoverCoverageResult(CoverageProcessor.java:732)
at com.aixcc.jenkins.harnesses.three.JenkinsThree.testRecoverCoverage(JenkinsThree.java:181)
at com.aixcc.jenkins.harnesses.three.JenkinsThree.fuzz(JenkinsThree.java:109)
at com.aixcc.jenkins.harnesses.three.JenkinsThree.fuzzerTestOneInput(JenkinsThree.java:76)
"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "JavaException.java.io.StreamCorruptedException"


def test_detect_fuzzer_security_issue_critical_file_path_traversal():
    """Test detection of JazzerSanitizer File Path Traversal."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueCritical: File path traversal: /tmp/expander-tmp1812740994817808216/output/q/r/s/t/../../jazzer-traversal"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "FilePathTraversal"


def test_detect_fuzzer_security_issue_critical_file_path_traversal_simple():
    """Test detection of JazzerSanitizer File Path Traversal (simple case)."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueCritical: File path traversal: /writable/../jazzer-traversal"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "FilePathTraversal"


@pytest.mark.skip(reason="Integer Overflow is deprecated after ASC.")
def test_detect_fuzzer_security_issue_critical_integer_overflow_addition():
    """Test detection of JazzerSanitizer Integer Overflow (addition)."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueCritical: Integer Overflow(addition) detected! REASON: 2147483647 + 1 == (int)-2147483648 != (long)2147483648"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "IntegerOverflow"


@pytest.mark.skip(reason="Integer Overflow is deprecated after ASC.")
def test_detect_fuzzer_security_issue_critical_integer_overflow_multiplication():
    """Test detection of JazzerSanitizer Integer Overflow (multiplication)."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueCritical: Integer Overflow(multiplication) detected! REASON: 6619136 * 13063935 == (int)1385889792 != (long)86471962460160"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "IntegerOverflow"


def test_detect_fuzzer_security_issue_critical_ldap_injection():
    """Test detection of JazzerSanitizer LDAP Injection."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueCritical: LDAP Injection"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "LdapInjection"


def test_detect_fuzzer_security_issue_critical_os_command_injection():
    """Test detection of JazzerSanitizer OS Command Injection."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueCritical: OS Command Injection"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "OsCommandInjection"


def test_detect_fuzzer_security_issue_critical_remote_jndi_lookup():
    """Test detection of JazzerSanitizer Remote JNDI Lookup."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueCritical: Remote JNDI Lookup"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "RemoteJndiLookup"


def test_detect_fuzzer_security_issue_critical_script_engine_injection():
    """Test detection of JazzerSanitizer Script Engine Injection."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueCritical: Script Engine Injection: Insecure user input was used in script engine invocation."""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "ScriptEngineInjection"


def test_detect_fuzzer_security_issue_high_load_arbitrary_library():
    """Test detection of JazzerSanitizer Load Arbitrary Library."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueHigh: load arbitrary library"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "ReflectiveCall"


def test_detect_fuzzer_security_issue_high_sql_injection():
    """Test detection of JazzerSanitizer SQL Injection."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueHigh: SQL Injection"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "SQLInjection"


def test_detect_fuzzer_security_issue_low_out_of_memory():
    """Test detection of JazzerSanitizer Out of Memory."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueLow: Out of memory (use '-Xmx1710m' to reproduce)"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "OutOfMemory"


def test_detect_fuzzer_security_issue_low_stack_overflow():
    """Test detection of JazzerSanitizer Stack Overflow."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueLow: Stack overflow (use '-Xss921k' to reproduce)"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "StackOverflow"


def test_detect_fuzzer_security_issue_medium_ssrf():
    """Test detection of JazzerSanitizer Server Side Request Forgery (SSRF)."""
    output = """== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueMedium: Server Side Request Forgery (SSRF)"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "ServerSideRequestForgery"


def test_detect_array_index_out_of_bounds_exception():
    """Test detection of JazzerSanitizer ArrayIndexOutOfBoundsException."""
    output = """== Java Exception: java.lang.ArrayIndexOutOfBoundsException: Index 200 out of bounds for length 200"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "JavaException.java.lang.ArrayIndexOutOfBoundsException"


def test_detect_array_index_out_of_bounds_exception_zero_length():
    """Test detection of JazzerSanitizer ArrayIndexOutOfBoundsException (zero length)."""
    output = """== Java Exception: java.lang.ArrayIndexOutOfBoundsException: Index 0 out of bounds for length 0"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "JavaException.java.lang.ArrayIndexOutOfBoundsException"


def test_detect_snappy_error():
    """Test detection of JazzerSanitizer SnappyError."""
    output = """== Java Exception: org.xerial.snappy.SnappyError: [INVALID_CHUNK_SIZE] Requested array size exceeds VM limit"""
    triggered, sanitizer_type = JazzerSanitizer.detect(output)
    assert triggered
    assert sanitizer_type == "JavaException.org.xerial.snappy.SnappyError"


if __name__ == "__main__":
    pytest.main()
