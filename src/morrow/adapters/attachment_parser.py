"""One bounded decoder process. No database, credentials, or project access."""

import base64
import io
import json
import sys
import warnings
from contextlib import closing

from morrow.core.attachments import (
    IMAGE_TYPES,
    MAX_FILE_BYTES,
    MAX_PAGES,
    MAX_PIXELS,
    MAX_PRODUCT_BYTES,
    MAX_TEXT_CHARS,
)


def parse(content, media_type):
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError("File byte limit exceeded")
    if media_type == "text/plain":
        text = content.decode("utf-8-sig")
        if any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
            raise ValueError("File is not UTF-8 text")
        return {
            "reading": "text",
            "pages": 0,
            "omitted_chars": max(0, len(text) - MAX_TEXT_CHARS),
            "parts": [("text/plain", None, text[:MAX_TEXT_CHARS].encode(), False, None, None)],
        }
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    if media_type in IMAGE_TYPES:
        with Image.open(io.BytesIO(content), formats=["PNG", "JPEG", "WEBP"]) as image:
            if Image.MIME[image.format] != media_type or image.width * image.height > MAX_PIXELS:
                raise ValueError("Image type or pixel limit does not match")
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Animated images are unsupported")
            image.load()
            converted = image.convert("RGB")
            output = io.BytesIO()
            converted.save(output, format="PNG")
            width, height = converted.size
            if converted is not image:
                converted.close()
        return {
            "reading": "image",
            "pages": 0,
            "omitted_chars": 0,
            "parts": [("image/png", None, output.getvalue(), False, width, height)],
        }
    if media_type != "application/pdf" or not content.startswith(b"%PDF-"):
        raise ValueError("Unsupported file content")
    import pypdfium2 as pdfium

    parts = []
    remaining = MAX_TEXT_CHARS
    omitted = 0
    text_pages = image_pages = 0
    with pdfium.PdfDocument(content) as document:
        if not 1 <= len(document) <= MAX_PAGES:
            raise ValueError("PDF exceeds the page limit")
        pages = len(document)
        for index in range(pages):
            with closing(document[index]) as page:
                width, height = page.get_size()
                if not 0 < width <= 20000 or not 0 < height <= 20000:
                    raise ValueError("PDF page dimensions exceed the limit")
                with closing(page.get_textpage()) as textpage:
                    if textpage.count_chars() > MAX_FILE_BYTES:
                        raise ValueError("PDF text exceeds extraction limit")
                    text = textpage.get_text_bounded().strip()
                if text:
                    selected = text[:remaining]
                    remaining -= len(selected)
                    omitted += len(text) - len(selected)
                    if selected:
                        parts.append(
                            ("text/plain", index + 1, selected.encode(), False, None, None)
                        )
                    text_pages += 1
                else:
                    image_pages += 1
                # A preview for every page; only scan pages are sent as images.
                scale = min(1.5, 1400 / max(width, height))
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    try:
                        output = io.BytesIO()
                        image.save(output, format="PNG")
                        width, height = image.size
                    finally:
                        image.close()
                finally:
                    bitmap.close()
                parts.append(("image/png", index + 1, output.getvalue(), bool(text), width, height))
                if sum(len(p[2]) for p in parts) > MAX_PRODUCT_BYTES:
                    raise ValueError("PDF products exceed the limit")
    reading = (
        "pdf_mixed" if text_pages and image_pages else "pdf_text" if text_pages else "pdf_images"
    )
    return {"reading": reading, "pages": pages, "omitted_chars": omitted, "parts": parts}


def main():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_PRODUCT_BYTES, MAX_PRODUCT_BYTES))
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    try:
        result = parse(sys.stdin.buffer.read(MAX_FILE_BYTES + 1), sys.argv[1])
        if sum(len(p[2]) for p in result["parts"]) > MAX_PRODUCT_BYTES:
            raise ValueError("Parsed products exceed limit")
        result["parts"] = [
            (mime, page, base64.b64encode(data).decode(), preview, width, height)
            for mime, page, data, preview, width, height in result["parts"]
        ]
        output = json.dumps(result).encode()
        if len(output) > MAX_PRODUCT_BYTES * 2:
            raise ValueError("Parsed products exceed limit")
        sys.stdout.buffer.write(output)
    except Exception:
        # Never expose decoder exception text, document data, or traceback.
        sys.stdout.write('{"error":"File is damaged, unsupported, or exceeds parsing limits"}')
        sys.exit(1)


if __name__ == "__main__":
    main()
