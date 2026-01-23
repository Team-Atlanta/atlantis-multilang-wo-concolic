import pytest

from mlla.modules.sanitizer import BaseSanitizer, GenericSanitizer, JazzerSanitizer


def test_detect_libfuzzer_timeout_jvm():
    """Test detection of libFuzzer timeout in JVM applications."""
    output = """==25210== ERROR: libFuzzer: timeout after 25 seconds

Stack traces of all JVM threads:
Thread[Reference Handler,10,system]
        at java.base@17.0.14/java.lang.ref.Reference.waitForReferencePendingList(Native Method)
        at java.base@17.0.14/java.lang.ref.Reference.processPendingReferences(Reference.java:253)
        at java.base@17.0.14/java.lang.ref.Reference$ReferenceHandler.run(Reference.java:215)

Thread[Signal Dispatcher,9,system]

Thread[process reaper,10,system]
        at java.base@17.0.14/jdk.internal.misc.Unsafe.park(Native Method)
        at java.base@17.0.14/java.util.concurrent.locks.LockSupport.parkNanos(LockSupport.java:252)
        at java.base@17.0.14/java.util.concurrent.SynchronousQueue$TransferStack.transfer(SynchronousQueue.java:401)
        at java.base@17.0.14/java.util.concurrent.SynchronousQueue.poll(SynchronousQueue.java:903)
        at java.base@17.0.14/java.util.concurrent.ThreadPoolExecutor.getTask(ThreadPoolExecutor.java:1061)
        at java.base@17.0.14/java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1122)
        at java.base@17.0.14/java.util.concurrent.ThreadPoolExecutor$Worker.run(ThreadPoolExecutor.java:635)
        at java.base@17.0.14/java.lang.Thread.run(Thread.java:840)

Thread[main,5,main]
        at app//com.code_intelligence.jazzer.driver.FuzzTargetRunner.dumpAllStackTraces(FuzzTargetRunner.java:536)

Thread[Common-Cleaner,8,InnocuousThreadGroup]
        at java.base@17.0.14/java.lang.Object.wait(Native Method)
        at java.base@17.0.14/java.lang.ref.ReferenceQueue.remove(ReferenceQueue.java:155)
        at java.base@17.0.14/jdk.internal.ref.CleanerImpl.run(CleanerImpl.java:140)
        at java.base@17.0.14/java.lang.Thread.run(Thread.java:840)
        at java.base@17.0.14/jdk.internal.misc.InnocuousThread.run(InnocuousThread.java:162)

Thread[Attach Listener,9,system]

Thread[Finalizer,8,system]
        at java.base@17.0.14/java.lang.Object.wait(Native Method)
        at java.base@17.0.14/java.lang.ref.ReferenceQueue.remove(ReferenceQueue.java:155)
        at java.base@17.0.14/java.lang.ref.ReferenceQueue.remove(ReferenceQueue.java:176)
        at java.base@17.0.14/java.lang.ref.Finalizer$FinalizerThread.run(Finalizer.java:172)
"""  # noqa: E501
    # Test JazzerSanitizer detection
    triggered_jazzer, sanitizer_type_jazzer = JazzerSanitizer.detect(output)
    assert not triggered_jazzer
    assert not sanitizer_type_jazzer

    # Test GenericSanitizer detection
    triggered_generic, sanitizer_type_generic = GenericSanitizer.detect(output)
    assert triggered_generic
    assert sanitizer_type_generic == "timeout"

    # Test BaseSanitizer.detect_crash_type
    triggered_base, sanitizer_type_base = BaseSanitizer.detect_crash_type(output)
    assert triggered_base
    assert sanitizer_type_base == "GenericSanitizer.timeout"


def test_detect_libfuzzer_timeout_non_jvm():
    """Test detection of libFuzzer timeout in non-JVM applications."""
    output = """==25210== ERROR: libFuzzer: timeout after 25 seconds

Some other stack trace without JVM threads
"""
    # Test GenericSanitizer detection
    triggered_generic, sanitizer_type_generic = GenericSanitizer.detect(output)
    assert triggered_generic
    assert sanitizer_type_generic == "timeout"


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
