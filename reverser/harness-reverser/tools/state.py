def merge_with_update(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return right

def merge_dict_with_update(left, right):
    if left is None:
        return right
    if right is None:
        return left
    for key, value in right.items():
        if key in left:
            left[key] = merge_with_update(left[key], value)
        else:
            left[key] = value
    return left

def merge_set_with_update(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return left.union(right)

def always_update(left, right):
    return right
