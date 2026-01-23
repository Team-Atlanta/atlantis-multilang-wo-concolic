import random
from io import BytesIO
from typing import Literal

from PIL import Image


def generate_random_image(
    encoder_name: Literal["gif", "png", "jpeg", "bmp"], width=256, height=256
) -> bytes:
    image = Image.new("RGB", (width, height))

    pixels = [
        (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        for _ in range(width * height)
    ]

    image.putdata(pixels)

    buf = BytesIO()
    image.save(buf, encoder_name)
    return buf.getvalue()
