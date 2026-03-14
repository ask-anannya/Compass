import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak
)
from knowledge.graph import load

NAVY   = colors.HexColor('#1B3A5C')
ACCENT = colors.HexColor('#2E86C1')
LIGHT  = colors.HexColor('#EBF5FB')
DARK   = colors.HexColor('#2C3E50')
MONO   = 'Courier'


def generate_pdf(session_id: str) -> bytes:
    graph = load(session_id)
    if not graph:
        raise ValueError('Session not found')

    arch        = graph['architecture']
    if isinstance(arch, list):
        arch = arch[0]
    features    = graph['features']
    per_file    = graph['per_file']
    connections = graph['connections']

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm,  bottomMargin=2*cm
    )

    title_style = ParagraphStyle('Title', fontSize=24, leading=30, textColor=NAVY, spaceAfter=8, fontName='Helvetica-Bold')
    h1_style    = ParagraphStyle('H1',    fontSize=16, textColor=NAVY,   spaceAfter=6,  spaceBefore=16, fontName='Helvetica-Bold')
    h2_style    = ParagraphStyle('H2',    fontSize=12, textColor=ACCENT, spaceAfter=4,  spaceBefore=10, fontName='Helvetica-Bold')
    body_style  = ParagraphStyle('Body',  fontSize=10, textColor=DARK,   spaceAfter=4,  leading=14)
    mono_style  = ParagraphStyle('Mono',  fontSize=9,  fontName=MONO,    textColor=DARK, spaceAfter=2)

    def tbl_style():
        return TableStyle([
            ('BACKGROUND',     (0, 0), (-1,  0),  NAVY),
            ('TEXTCOLOR',      (0, 0), (-1,  0),  colors.white),
            ('FONTNAME',       (0, 0), (-1,  0),  'Helvetica-Bold'),
            ('FONTSIZE',       (0, 0), (-1,  0),  10),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),  [LIGHT, colors.white]),
            ('FONTSIZE',       (0, 1), (-1, -1),  9),
            ('GRID',           (0, 0), (-1, -1),  0.5, colors.HexColor('#BDC3C7')),
            ('VALIGN',         (0, 0), (-1, -1),  'TOP'),
            ('LEFTPADDING',    (0, 0), (-1, -1),  6),
            ('RIGHTPADDING',   (0, 0), (-1, -1),  6),
            ('TOPPADDING',     (0, 0), (-1, -1),  4),
            ('BOTTOMPADDING',  (0, 0), (-1, -1),  4),
        ])

    story = []

    # ── Cover ─────────────────────────────────────────────────────────────────
    story.append(Paragraph('Codebase Intelligence Report', title_style))
    story.append(Paragraph(
        f"Architecture: {arch['pattern']}  ·  Language: {arch['language']}  ·  Framework: {arch['framework']}",
        body_style
    ))
    story.append(HRFlowable(width='100%', color=ACCENT, spaceAfter=12))

    # ── Architecture overview ─────────────────────────────────────────────────
    story.append(Paragraph('Architecture Overview', h1_style))
    byta = arch['before_you_touch_anything']
    if isinstance(byta, list):
        byta = ' '.join(byta)
    story.append(Paragraph(byta, body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph('Conventions', h2_style))
    for c in arch['conventions']:
        story.append(Paragraph(f'• {c}', body_style))

    story.append(Paragraph('Rules', h2_style))
    for r in arch['rules']:
        story.append(Paragraph(f'• {r}', body_style))

    story.append(PageBreak())

    # ── Feature map ───────────────────────────────────────────────────────────
    story.append(Paragraph('Feature Map', h1_style))
    feat_data = [['Feature', 'Entry Point', 'Files', 'Description']]
    for f in features:
        feat_data.append([
            Paragraph(f['feature'],                        mono_style),
            Paragraph(f['entry_point'],                    mono_style),
            Paragraph('\n'.join(f['files'][:5]),           mono_style),
            Paragraph(f['description'] if isinstance(f['description'], str) else ' '.join(f['description']), body_style),
        ])
    feat_table = Table(feat_data, colWidths=[3*cm, 4*cm, 4.5*cm, 5.5*cm])
    feat_table.setStyle(tbl_style())
    story.append(feat_table)

    story.append(PageBreak())

    # ── File breakdown ────────────────────────────────────────────────────────
    story.append(Paragraph('File Breakdown', h1_style))
    story.append(Paragraph(
        'Every file in the repository: purpose, what controls it, what it calls, key functions.',
        body_style
    ))
    story.append(Spacer(1, 8))

    conn_lookup = {c['path']: c for c in connections}

    file_data = [['File', 'Purpose', 'Called By', 'Calls', 'Key Functions']]
    for f in per_file:
        conn = conn_lookup.get(f['path'], {})
        file_data.append([
            Paragraph(f['path'],                                mono_style),
            Paragraph(f['purpose'],                             body_style),
            Paragraph('\n'.join(conn.get('called_by', [])[:3]), mono_style),
            Paragraph('\n'.join(conn.get('calls',     [])[:3]), mono_style),
            Paragraph('\n'.join(f.get('functions',    [])[:5]), mono_style),
        ])

    file_table = Table(file_data, colWidths=[3.5*cm, 4.5*cm, 3*cm, 3*cm, 3*cm])
    file_table.setStyle(tbl_style())
    story.append(file_table)

    doc.build(story)
    return buffer.getvalue()
