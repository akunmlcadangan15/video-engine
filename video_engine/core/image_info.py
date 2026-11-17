"""Image metadata without renderer initialization."""
from pathlib import Path

def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as im:
        return im.size
