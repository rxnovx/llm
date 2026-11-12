"""Render HTML, Word, and PowerPoint artifacts from bounded model data."""
from html import escape
from io import BytesIO
from pathlib import Path
import re


def build_html(name, content, table):
    source = content.strip()
    fenced = re.fullmatch(r"```html[ \t]*\r?\n(.*?)\r?\n```", source, re.I | re.S)
    if fenced:
        source = fenced[1].strip()
    if re.match(r"<!doctype\s+html\b|<html(?=[\s>])", source, re.I):
        if table:
            raise ValueError('Put the table inside the HTML document.')
        return source.encode('utf-8-sig')

    title = escape(Path(name).stem)
    markup = fenced or re.match(r"<[a-z][\w:-]*(?=[\s/>])", source, re.I)
    body = source if markup else f"<h1>{title}</h1><pre>{escape(source)}</pre>"
    if table:
        rendered = []
        for index, row in enumerate(table):
            tag = 'th' if index == 0 else 'td'
            cells = ''.join(f'<{tag}>{escape(str(value)) if value is not None else ""}</{tag}>' for value in row)
            rendered.append(f'<tr>{cells}</tr>')
        body += '<table>' + ''.join(rendered) + '</table>'
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{title}</title><style>'
        'body{margin:3rem auto;padding:0 1rem;max-width:64rem;font:1rem/1.6 system-ui,sans-serif}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}'
        'table{border-collapse:collapse}td,th{border:1px solid #999;padding:.35rem .65rem}'
        '</style></head><body>' + body + '</body></html>'
    ).encode()


def html_preview(raw: bytes) -> str:
    """Keep the preview offline and inert while preserving the original download."""
    from lxml import etree, html

    document = html.document_fromstring(raw.decode('utf-8-sig'), parser=html.HTMLParser(no_network=True))
    removed_tags = {
        'script', 'iframe', 'frame', 'frameset', 'object', 'embed', 'base', 'meta', 'link', 'template',
        'animate', 'animatecolor', 'animatemotion', 'animatetransform', 'set', 'discard',
    }
    for node in list(document.iter('*')):
        if node.tag.lower() in removed_tags:
            node.drop_tree()
            continue
        for attribute, value in list(node.attrib.items()):
            if attribute.startswith('on') or attribute in {'action', 'formaction', 'ping', 'srcdoc', 'target'}:
                del node.attrib[attribute]
            elif attribute in {'href', 'xlink:href'}:
                if not value.startswith('#'):
                    del node.attrib[attribute]
                elif node.tag == 'a':
                    node.attrib[attribute] = 'about:srcdoc' + value

    head = document.find('head')
    if head is None:
        head = etree.Element('head')
        document.insert(0, head)
    policy = etree.Element('meta')
    policy.set('http-equiv', 'Content-Security-Policy')
    policy.set('content', "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
                          "base-uri 'none'; form-action 'none'")
    head.insert(0, policy)
    head.insert(1, etree.Element('meta', name='viewport', content='width=device-width, initial-scale=1'))
    return '<!doctype html>' + html.tostring(document, encoding='unicode')


def build_docx(name, content, table):
    from docx import Document

    document = Document()
    document.add_heading(Path(name).stem, level=0)
    for line in content.splitlines():
        if heading := re.fullmatch(r'(#{1,6})\s+(.+)', line.strip()):
            document.add_heading(heading[2], level=len(heading[1]))
        elif bullet := re.fullmatch(r'[-*+]\s+(.+)', line.strip()):
            document.add_paragraph(bullet[1], style='List Bullet')
        elif line.strip():
            document.add_paragraph(line)
    if table:
        grid = document.add_table(rows=0, cols=len(table[0]), style='Table Grid')
        for index, row in enumerate(table):
            for cell, value in zip(grid.add_row().cells, row):
                cell.paragraphs[0].add_run(str(value) if value is not None else '').bold = index == 0
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def build_pptx(slides):
    from pptx import Presentation
    from pptx.enum.text import MSO_AUTO_SIZE
    from pptx.util import Inches, Pt

    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    for item in slides:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        title = slide.shapes.add_textbox(Inches(.9), Inches(.6), Inches(11.5), Inches(1.35)).text_frame
        title.word_wrap = True
        title.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        title.text = item.title
        title.paragraphs[0].font.size = Pt(34)
        title.paragraphs[0].font.bold = True
        if item.bullets:
            body = slide.shapes.add_textbox(Inches(.9), Inches(2), Inches(11.5), Inches(4.85)).text_frame
            body.word_wrap = True
            body.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            for index, bullet in enumerate(item.bullets):
                paragraph = body.add_paragraph() if index else body.paragraphs[0]
                paragraph.text = '•  ' + bullet
                paragraph.font.size = Pt(22)
                paragraph.space_after = Pt(10)
        if item.notes:
            slide.notes_slide.notes_text_frame.text = item.notes
    output = BytesIO()
    deck.save(output)
    return output.getvalue()
