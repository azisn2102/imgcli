from imgcli.cli import parse_ratio, size_from_ratio
from imgcli.image import detect_ext, jpeg_size, slugify


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("   ") == "image"
    assert slugify("A" * 100) == "a" * 48


def test_detect_ext():
    assert detect_ext(b"\xff\xd8\xff\xe0") == ".jpg"
    assert detect_ext(b"\x89PNG\r\n\x1a\n") == ".png"
    assert detect_ext(b"GIF89a") == ".gif"
    assert detect_ext(b"RIFF\x00\x00\x00\x00WEBP") == ".webp"
    assert detect_ext(b"????") == ".img"


def test_jpeg_size():
    # SOI + a minimal but well-formed SOF0 segment declaring 16x24
    sof0 = b"\xff\xc0\x00\x11\x08" + (24).to_bytes(2, "big") + (16).to_bytes(2, "big") + b"\x03" + b"\x00" * 9
    data = b"\xff\xd8" + sof0
    assert jpeg_size(data) == (16, 24)
    assert jpeg_size(b"\xff\xd8\xff\xd9") is None


def test_parse_ratio():
    assert parse_ratio("16:9") == (16, 9)
    assert parse_ratio("1x1") == (1, 1)


def test_size_from_ratio():
    assert size_from_ratio(1, 1) == (1024, 1024)
    w, h = size_from_ratio(16, 9)
    assert w == 1024 and h == 576
    w, h = size_from_ratio(9, 16)
    assert h == 1024 and w == 576
