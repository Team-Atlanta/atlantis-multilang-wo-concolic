def merge_with_update(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return right


def always_update(left, right):
    return right
