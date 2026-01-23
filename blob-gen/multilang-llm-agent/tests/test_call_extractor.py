from mlla.utils.bit import LocationInfo
from mlla.utils.call_extractor import get_all_calls
from mlla.utils.cg import FuncInfo


def _get_location_info(file_path: str) -> LocationInfo:
    return LocationInfo(
        file_path=file_path, start_line=0, end_line=1, func_name="dummy"
    )


def test_empty():
    func_info = FuncInfo(
        func_location=_get_location_info("test.c"),
        func_body="""
    int main() {
        return 0;
    }
    """,
    )

    _result, _ = get_all_calls(func_info.func_location.file_path, func_info.func_body)
    result = [node.text.decode("utf8") for node in _result if node.text]
    assert len(result) == 0
    result = [node.range.start_point.row for node in _result if node.text]
    assert result == []


def test_c():
    func_info = FuncInfo(
        func_location=_get_location_info("test.c"),
        func_body="""
    int main() {
        printf("Hello");
        foo();
        bar(1, 2);
        return 0;
    }
    """,
    )

    _result, _ = get_all_calls(func_info.func_location.file_path, func_info.func_body)
    result = [node.text.decode("utf8") for node in _result if node.text]
    assert set(result) == set(["printf", "foo", "bar"])
    result = [node.range.start_point.row for node in _result if node.text]
    assert set(result) == set([2, 3, 4])


def test_cpp():
    func_info = FuncInfo(
        func_location=_get_location_info("test.cpp"),
        func_body="""
    void MyClass::func() {
        this->memberFunc();
        staticFunc();
        std::printf("Hello");
    }
    """,
    )
    _result, _ = get_all_calls(func_info.func_location.file_path, func_info.func_body)
    result = [node.text.decode("utf8") for node in _result if node.text]
    assert set(result) == set(["staticFunc", "memberFunc", "printf"])
    result = [node.range.start_point.row for node in _result if node.text]
    assert set(result) == set([2, 3, 4])


def test_java():
    func_info = FuncInfo(
        func_location=_get_location_info("test.java"),
        func_body="""
    public class MyClass {
        public void myMethod() {
            System.out.println("Hello");
            foo();
            bar(1, 2);
        }
    }
    """,
    )
    _result, _ = get_all_calls(func_info.func_location.file_path, func_info.func_body)
    result = [node.text.decode("utf8") for node in _result if node.text]
    assert set(result) == set(["println", "foo", "bar"])
    result = [node.range.start_point.row for node in _result if node.text]
    assert set(result) == set([5, 3, 4])


def test_java2():
    func_info = FuncInfo(
        func_location=_get_location_info("test.java"),
        func_body="""public TarFile(final byte[] content) throws IOException {
        this(new SeekableInMemoryByteChannel(content));
    }
    """,
    )
    _result, _ = get_all_calls(func_info.func_location.file_path, func_info.func_body)
    result = [node.text.decode("utf8") for node in _result if node.text]
    print(result)
    assert set(result) == set(["SeekableInMemoryByteChannel", "this"])
    result = [node.range.start_point.row for node in _result if node.text]
    assert set(result) == set([2])
