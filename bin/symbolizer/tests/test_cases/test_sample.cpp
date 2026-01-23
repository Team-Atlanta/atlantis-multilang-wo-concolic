int global_function(int x) {
    return x * 2;
}

namespace test {
    int namespaced_function() {
        return 42;
    }
}

class TestClass {
public:
    TestClass() {}
    ~TestClass() {}
    
    int method() {
        return 1;
    }
    
    static int static_method() {
        return 2;
    }
};

template<typename T>
T template_function(T value) {
    return value;
}
