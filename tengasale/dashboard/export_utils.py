import html
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone


def dated_filename(slug, extension):
    return f"tengasale-{slug}-{timezone.localdate().isoformat()}.{extension}"


def export_pdf_response(title, columns, rows, *, filename, filters=None, summary=None, landscape=False):
    """
    Render a compact HQ table export as PDF.
    Keep the HTML deliberately simple because xhtml2pdf supports a small CSS subset.
    """
    filters = filters or []
    summary = summary or []
    page_size = "A4 landscape" if landscape else "A4"
    generated_at = timezone.localtime().strftime("%Y-%m-%d %H:%M")

    def esc(value):
        if value is None:
            return ""
        return html.escape(str(value))

    header_cells = "".join(f"<th>{esc(col)}</th>" for col in columns)
    body_rows = []
    for row in rows:
        body_rows.append("<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>")
    if not body_rows:
        body_rows.append(f"<tr><td colspan='{len(columns)}' class='empty'>No records found.</td></tr>")

    summary_html = "".join(f"<div class='metric'><span>{esc(label)}</span><strong>{esc(value)}</strong></div>" for label, value in summary)
    filters_html = "".join(f"<span>{esc(label)}: <b>{esc(value or 'All')}</b></span>" for label, value in filters)

    document = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          @page {{ size: {page_size}; margin: 22px; }}
          body {{ font-family: Helvetica, Arial, sans-serif; color: #0f172a; font-size: 8px; }}
          .brand {{ color: #ff5a00; font-size: 10px; font-weight: bold; text-transform: uppercase; }}
          h1 {{ font-size: 18px; margin: 4px 0 2px; }}
          .meta {{ color: #64748b; margin-bottom: 12px; }}
          .filters span {{ display: inline-block; margin: 0 8px 6px 0; color: #475569; }}
          .summary {{ margin: 8px 0 12px; }}
          .metric {{ display: inline-block; border: 1px solid #e2e8f0; padding: 6px 8px; margin: 0 6px 6px 0; }}
          .metric span {{ display: block; color: #64748b; font-size: 7px; text-transform: uppercase; }}
          .metric strong {{ display: block; font-size: 11px; margin-top: 2px; }}
          table {{ width: 100%; border-collapse: collapse; }}
          th {{ background: #f8fafc; color: #334155; font-size: 6px; text-transform: uppercase; border: 1px solid #e2e8f0; padding: 2px; }}
          td {{ border: 1px solid #edf2f7; padding: 2px; vertical-align: top; }}
          tr:nth-child(even) td {{ background: #fbfdff; }}
          .empty {{ text-align: center; color: #64748b; padding: 18px; }}
        </style>
      </head>
      <body>
        <div class="brand">TengaSale HQ Export</div>
        <h1>{esc(title)}</h1>
        <div class="meta">Generated {esc(generated_at)}</div>
        <div class="filters">{filters_html}</div>
        <div class="summary">{summary_html}</div>
        <table>
          <thead><tr>{header_cells}</tr></thead>
          <tbody>{''.join(body_rows)}</tbody>
        </table>
      </body>
    </html>
    """

    buffer = BytesIO()
    try:
        from xhtml2pdf import pisa

        result = pisa.CreatePDF(document, dest=buffer, encoding="utf-8")
        if result.err:
            raise RuntimeError("xhtml2pdf failed")
    except Exception as exc:
        response = HttpResponse(f"PDF export failed: {exc}", status=503, content_type="text/plain")
        return response

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
