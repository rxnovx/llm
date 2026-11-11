import io
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from test_chat import auth
from test_knowledge import base, index, upload


@pytest.mark.parametrize('suffix', ['png', 'pdf'])
def test_local_ocr_indexes_image_text_and_page_location(client, account, suffix):
    if not shutil.which('tesseract') or suffix == 'pdf' and not shutil.which('pdftoppm'):
        pytest.skip('Tesseract and Poppler binaries are required for the OCR integration test')
    image = Image.new('RGB', (1100, 230), 'white')
    ImageDraw.Draw(image).text((50, 65), 'OFFLINE SEARCH TEST', font=ImageFont.load_default(size=82), fill='black')
    raw = io.BytesIO()
    if suffix == 'pdf':
        canvas = Canvas(raw, pagesize=(1100, 230))
        canvas.drawImage(ImageReader(image), 0, 0, width=1100, height=230)
        canvas.save()
    else:
        image.save(raw, format='PNG')
    headers = auth(account())
    identifier = base(client, headers)
    doc = upload(client, headers, base_id=identifier, filename='scan.' + suffix, raw=raw.getvalue())
    index(client)
    result = client.get('/knowledge/documents/' + doc['id'] + '/chunks', headers=headers)
    assert result.status_code == 200, result.text
    assert 'OFFLINE SEARCH TEST' in result.json()['items'][0]['content']
    location = result.json()['items'][0]['location']
    assert 'OCR' in location and (suffix != 'pdf' or '1쪽' in location)
