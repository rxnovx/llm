"""Offline OCR for bounded images and image-only PDF pages."""

import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

IMAGE_FORMATS = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff'}
MAX_PAGES = 8
MAX_PIXELS = 16_000_000


def installed():
    return bool(shutil.which('tesseract'))


def run(args, deadline, data=None):
    try:
        completed = subprocess.run(
            args, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=True, timeout=max(0.1, min(18, deadline - time.monotonic())),
        )
        return completed.stdout
    except FileNotFoundError:
        raise ValueError('OCR에는 Tesseract와 PDF 렌더러 설치가 필요합니다.') from None
    except subprocess.TimeoutExpired:
        raise ValueError('OCR 시간이 초과되었습니다. 문서 또는 이미지를 나누어 주세요.') from None
    except subprocess.CalledProcessError:
        raise ValueError('OCR 처리에 실패했습니다. 파일 형식과 설치한 OCR 언어를 확인하세요.') from None


def image_text(raw, languages, deadline):
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > MAX_PIXELS or getattr(image, 'n_frames', 1) > 1:
                raise ValueError('OCR은 1,600만 픽셀 이하의 단일 이미지를 지원합니다.')
            image = ImageOps.exif_transpose(image).convert('L')
            image.thumbnail((2600, 2600))
            data = io.BytesIO()
            image.save(data, format='PNG')
    except (OSError, Image.DecompressionBombError):
        raise ValueError('읽을 수 있는 이미지 파일인지 확인하세요.') from None
    return run(['tesseract', 'stdin', 'stdout', '-l', languages], deadline, data.getvalue()).decode('utf-8', errors='replace')


def pdf_text(raw, pages, languages):
    if len(pages) > MAX_PAGES:
        raise ValueError(f'한 파일에서 OCR로 읽을 수 있는 PDF는 {MAX_PAGES}쪽입니다. 파일을 나누어 주세요.')
    deadline = time.monotonic() + 65
    with tempfile.TemporaryDirectory(prefix='llm-ocr-') as directory:
        source = Path(directory) / 'document.pdf'
        source.write_bytes(raw)
        for page in pages:
            if time.monotonic() >= deadline:
                raise ValueError('PDF OCR 시간이 초과되었습니다. 파일을 나누어 주세요.')
            image = run(['pdftoppm', '-f', str(page), '-l', str(page),
                         '-scale-to', '2300', '-png', '-singlefile', str(source)], deadline)
            yield page, image_text(image, languages, deadline)
