import base64
import io
import json
import re
import urllib.request
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, Image as RLImage
)
from PIL import Image as PILImage
from knowledge.graph import load

LOGO_PATH = r'D:\Gemini live\ios-compass-7.png'


def _logo_reader() -> ImageReader:
    """Invert the logo (black→white) and make the background transparent."""
    img = PILImage.open(LOGO_PATH).convert('RGBA')
    r, g, b, a = img.split()

    # Invert RGB so black circle → white, white bg → black
    def invert_channel(ch):
        from PIL import ImageOps
        return ImageOps.invert(ch)

    r, g, b = invert_channel(r), invert_channel(g), invert_channel(b)
    img = PILImage.merge('RGBA', (r, g, b, a))

    # Make near-black pixels (the original white bg, now inverted to black) transparent
    pixels = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            pr, pg, pb, pa = pixels[x, y]
            if pr < 40 and pg < 40 and pb < 40:
                pixels[x, y] = (pr, pg, pb, 0)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return ImageReader(buf)

def _nid(s: str) -> str:
    """Sanitise a string into a valid Mermaid node ID."""
    return re.sub(r'[^a-zA-Z0-9_]', '_', s)[:40]


def _lbl(s: str) -> str:
    """Sanitise a string for use as a Mermaid node label (inside quotes)."""
    return s.replace('\\', '/').replace('"', "'")


def _build_mermaid(graph: dict) -> str:
    """Generate a Mermaid flowchart from the knowledge graph."""
    arch = graph['architecture']
    if isinstance(arch, list):
        arch = arch[0]

    features    = graph['features']
    connections = graph['connections']

    lines = ['flowchart TD']

    # Entry-point cluster — prefix all IDs with "ep_" to avoid clashes
    entry_points = arch.get('entry_points', [])
    if entry_points:
        lines.append('    subgraph EP["Entry Points"]')
        for ep in entry_points[:4]:
            lines.append(f'        ep_{_nid(ep)}["{_lbl(ep)}"]')
        lines.append('    end')

    # One subgraph per feature — prefix file node IDs with feature ID
    feat_ids: dict[str, str] = {}
    for feat in features:
        fid = _nid(feat['feature'])
        feat_ids[feat['feature']] = fid
        lines.append(f'    subgraph {fid}["{_lbl(feat["feature"])}"]')
        for fpath in feat.get('files', [])[:3]:
            lines.append(f'        {fid}_{_nid(fpath)}["{_lbl(fpath)}"]')
        lines.append('    end')

    # Edges: entry-point → feature (via entry_point field)
    ep_set  = set(entry_points)
    linked: set[str] = set()

    for feat in features:
        ep = feat.get('entry_point', '')
        if ep in ep_set:
            key = f'ep_{_nid(ep)}->{feat_ids[feat["feature"]]}'
            if key not in linked:
                lines.append(f'    ep_{_nid(ep)} --> {feat_ids[feat["feature"]]}')
                linked.add(key)

    # Edges: cross-feature calls derived from connections
    file_to_feat = {fpath: feat['feature']
                    for feat in features
                    for fpath in feat.get('files', [])}

    for conn in connections:
        src_feat = file_to_feat.get(conn['path'])
        if not src_feat:
            continue
        for called in conn.get('calls', []):
            dst_feat = file_to_feat.get(called)
            if dst_feat and dst_feat != src_feat:
                key = f'{feat_ids.get(src_feat)}->{feat_ids.get(dst_feat)}'
                if key not in linked and feat_ids.get(src_feat) and feat_ids.get(dst_feat):
                    lines.append(f'    {feat_ids[src_feat]} --> {feat_ids[dst_feat]}')
                    linked.add(key)

    return '\n'.join(lines)


def _render_mermaid(mermaid_code: str) -> bytes | None:
    """Render Mermaid to PNG via mermaid.ink (GET, base64-encoded JSON)."""
    try:
        payload = json.dumps({'code': mermaid_code, 'mermaid': {'theme': 'default'}})
        encoded = base64.urlsafe_b64encode(payload.encode('utf-8')).decode('utf-8').rstrip('=')
        url = f'https://mermaid.ink/img/{encoded}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            print(f'[pdf] Mermaid render OK — {len(data)} bytes')
            return data
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        print(f'[pdf] Mermaid render HTTP {e.code}: {body[:200]}')
        return None
    except Exception as e:
        print(f'[pdf] Mermaid render failed: {type(e).__name__}: {e}')
        return None


PAGE_W, PAGE_H = A4

NAVY   = colors.HexColor('#1B3A5C')
ACCENT = colors.HexColor('#2E86C1')
LIGHT  = colors.HexColor('#EBF5FB')
DARK   = colors.HexColor('#2C3E50')
MONO   = 'Courier'


def _draw_cover(canvas, doc):
    """Full-page black cover with compass logo."""
    canvas.saveState()

    # Black background
    canvas.setFillColor(colors.black)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    # ── Logo ──────────────────────────────────────────────────────────────────
    logo      = _logo_reader()
    logo_size = 4 * cm
    cx        = PAGE_W / 2
    cy        = PAGE_H / 2 + 3.5 * cm
    canvas.drawImage(logo,
                     cx - logo_size / 2, cy - logo_size / 2,
                     width=logo_size, height=logo_size,
                     mask='auto')

    # ── Wordmark ──────────────────────────────────────────────────────────────
    canvas.setFillColor(colors.white)
    canvas.setFont('Helvetica-Bold', 52)
    canvas.drawCentredString(PAGE_W / 2, PAGE_H / 2 + 0.3 * cm, 'COMPASS')

    # Thin rule
    rule_w = 7 * cm
    canvas.setStrokeColor(colors.HexColor('#555555'))
    canvas.setLineWidth(0.5)
    canvas.line(PAGE_W / 2 - rule_w / 2, PAGE_H / 2 - 0.6 * cm,
                PAGE_W / 2 + rule_w / 2, PAGE_H / 2 - 0.6 * cm)

    # Subtitle
    canvas.setFillColor(colors.HexColor('#AAAAAA'))
    canvas.setFont('Helvetica', 13)
    canvas.drawCentredString(PAGE_W / 2, PAGE_H / 2 - 1.3 * cm,
                             'Codebase Intelligence Report')

    canvas.restoreState()


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

    # Cover page is drawn via onFirstPage callback; start content on page 2
    story.append(PageBreak())

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

    # ── Architecture diagram ───────────────────────────────────────────────────
    story.append(Paragraph('Architecture Diagram', h1_style))
    diagram_png = _render_mermaid(_build_mermaid(graph))
    if diagram_png:
        img_buf   = io.BytesIO(diagram_png)
        rl_img    = RLImage(img_buf)
        max_w     = PAGE_W - 4 * cm
        if rl_img.drawWidth > max_w:
            scale             = max_w / rl_img.drawWidth
            rl_img.drawWidth  = max_w
            rl_img.drawHeight = rl_img.drawHeight * scale
        story.append(rl_img)
    else:
        story.append(Paragraph('(Diagram unavailable — Kroki render failed)', body_style))

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

    doc.build(story, onFirstPage=_draw_cover)
    return buffer.getvalue()
