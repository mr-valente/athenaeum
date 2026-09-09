from pathlib import Path
import html
import re
import xml.etree.ElementTree as ET
import markdown
import reportlab
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    Preformatted,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/oracle-setup-guide.pdf'
fonts = Path(reportlab.__file__).parent / 'fonts'
for name, file in [('Vera', 'Vera.ttf'), ('VeraBold', 'VeraBd.ttf'),
                   ('VeraItalic', 'VeraIt.ttf'), ('VeraBI', 'VeraBI.ttf')]:
    pdfmetrics.registerFont(TTFont(name, str(fonts / file)))
pdfmetrics.registerFontFamily('Vera', normal='Vera', bold='VeraBold',
                             italic='VeraItalic', boldItalic='VeraBI')
INK = colors.HexColor('#26393f')
ACCENT = colors.HexColor('#236c67')
MUTED = colors.HexColor('#65767b')
PALE = colors.HexColor('#edf4f2')
body = ParagraphStyle('body', fontName='Vera', fontSize=9.1, leading=12.7,
                      textColor=INK, spaceAfter=8)
h1 = ParagraphStyle('h1', parent=body, fontName='VeraBold', fontSize=26,
                    leading=31, spaceAfter=15, textColor=ACCENT)
quote = ParagraphStyle('quote', parent=body, leftIndent=10, rightIndent=10,
                       borderPadding=8, backColor=PALE, spaceBefore=4, spaceAfter=12)
h3 = ParagraphStyle('h3', parent=body, fontName='VeraBold', fontSize=12,
                    leading=16, spaceBefore=10, spaceAfter=8, keepWithNext=True)
h2 = ParagraphStyle('h2', parent=body, fontName='VeraBold', fontSize=17,
                    leading=22, spaceBefore=3, spaceAfter=13, keepWithNext=True)
cell = ParagraphStyle('cell', parent=body, fontSize=8.1, leading=11.2,
                      spaceAfter=0)
th = ParagraphStyle('th', parent=cell, fontName='VeraBold', textColor=colors.white)
code = ParagraphStyle('code', fontName='Courier', fontSize=8.2, leading=11.0,
                      textColor=INK, spaceBefore=0, spaceAfter=0)

def inline(el):
    result = html.escape((el.text or '').replace('→', ' / '))
    for child in el:
        content = inline(child)
        if child.tag == 'strong':
            item = '<b>' + content + '</b>'
        elif child.tag == 'em':
            item = '<i>' + content + '</i>'
        elif child.tag == 'code':
            item = '<font name="Courier" size="8">' + content + '</font>'
        elif child.tag == 'a':
            href = html.escape(child.attrib['href'], quote=True)
            item = f'<a href="{href}" color="#236c67">{content}</a>'
        elif child.tag == 'br':
            item = '<br/>'
        else:
            item = content
        result += item + html.escape((child.tail or '').replace('→', ' / '))
    return result

story = []
for section_index, section in enumerate((ROOT / 'docs/guides/2-oracle-setup-guide.md').read_text().split('<!-- PAGEBREAK -->')):
    if section_index:
        story.append(PageBreak())
    dom = ET.fromstring('<div>' + markdown.markdown(section, extensions=['tables', 'fenced_code']) + '</div>')
    for el in dom:
        if el.tag in ('h1', 'h2', 'h3', 'p'):
            style = {'h1': h1, 'h2': h2, 'h3': h3}.get(el.tag, body)
            story.append(Paragraph(inline(el), style))
        elif el.tag == 'blockquote':
            for paragraph in el:
                story.append(Paragraph(inline(paragraph), quote))
        elif el.tag == 'pre':
            raw = ''.join(el.itertext()).rstrip()
            for line in raw.splitlines():
                assert pdfmetrics.stringWidth(line, 'Courier', 8.2) < 498, line
            box = Table([[Preformatted(raw, code)]], colWidths=[516])
            box.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), PALE),
                ('LEFTPADDING', (0,0), (-1,-1), 8),
                ('RIGHTPADDING', (0,0), (-1,-1), 8),
                ('TOPPADDING', (0,0), (-1,-1), 6),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                ('LINEBEFORE', (0,0), (0,-1), 1.5, ACCENT),
            ]))
            story.extend([box, Spacer(1, 10)])
        elif el.tag == 'table':
            rows = [[Paragraph(inline(td), th if td.tag == 'th' else cell)
                     for td in tr] for tr in el.findall('.//tr')]
            count = len(rows[0])
            widths = {2: [175, 341], 3: [80, 200, 236], 4: [50, 64, 224, 178]}[count]
            if count == 2 and 'Command' in ''.join(el.itertext()):
                widths = [238, 278]
            table = Table(rows, colWidths=widths, repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), INK),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 8),
                ('RIGHTPADDING', (0,0), (-1,-1), 8),
                ('TOPPADDING', (0,0), (-1,-1), 6),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [PALE, colors.HexColor('#f8faf9')]),
                ('LINEBELOW', (0,-1), (-1,-1), .4, colors.HexColor('#bdceca')),
            ]))
            story.extend([table, Spacer(1, 10)])
        elif el.tag in ('ul', 'ol'):
            for i, li in enumerate(el):
                story.append(Paragraph((f'{i+1}. ' if el.tag=='ol' else '• ') + inline(li), body))
        else:
            raise RuntimeError(el.tag)

def decorate(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(ACCENT)
    canvas.rect(48, 750, 22, 3, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor('#bdceca'))
    canvas.line(78, 751, 564, 751)
    canvas.setFont('VeraBold', 8)
    canvas.drawString(48, 763, 'ATHENAEUM / PART 2')
    canvas.setFillColor(MUTED)
    canvas.setFont('Vera', 7.1)
    canvas.drawString(48, 27, 'Ubuntu 26.04 ARM · Host setup · Deployment and recovery')
    canvas.drawRightString(564, 27, f'{doc.page:02d}')
    canvas.restoreState()

doc = SimpleDocTemplate(str(OUT), pagesize=(612, 792), leftMargin=48,
                        rightMargin=48, topMargin=57, bottomMargin=46,
                        title='Athenaeum — Part 2: Host Setup and Deployment',
                        author='Prepared for Nicholas Valente',
                        subject='Ubuntu 26.04 ARM host setup after Oracle provisioning')
doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
print(OUT)
