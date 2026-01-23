import testlang

def testlang_compare(tl1: str, tl2: str) -> bool:
    warning = testlang.validate(tl1)
    if warning:
        print(f"testlang 1 Warning: {warning}")
    warning = testlang.validate(tl2)
    if warning:
        print(f"testlang 2 Warning: {warning}")

    hash1 = testlang.hash(testlang.normalize(tl1))
    hash2 = testlang.hash(testlang.normalize(tl2))
    return hash1 == hash2

if __name__ == '__main__':
    # get two testlang files from args and compare
    import argparse
    parser = argparse.ArgumentParser(description='Compare two testlang files.')
    parser.add_argument('one', type=str, help='First testlang file')
    parser.add_argument('two', type=str, help='Second testlang file')
    args = parser.parse_args()

    one = open(args.one).read()
    two = open(args.two).read()

    if testlang_compare(one, two):
        print('Two files are equal.')
    else:
        print('Two files are different.')