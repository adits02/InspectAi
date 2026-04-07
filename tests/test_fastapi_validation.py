import os
import zipfile
from fastapi.testclient import TestClient
from fastapi_app import app
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

client = TestClient(app)


def create_pdf(path, lines):
    c = canvas.Canvas(path, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()


def test_api_validate_single_pdf(tmp_path):
    p = tmp_path / 'sample.pdf'
    create_pdf(str(p), ['Anti-Ragging Committee', 'This is an anti-ragging affidavit', 'Name: Jane'])

    with open(p, 'rb') as f:
        resp = client.post('/api/validate', files={'pdf': ('sample.pdf', f, 'application/pdf')}, data={'regulatory': ''})

    assert resp.status_code == 200
    data = resp.json()
    assert 'text_extraction' in data
    assert data['text_extraction'] is True


def test_api_validate_required_zip(tmp_path):
    # create PDFs
    p1 = tmp_path / 'anti_ragging.pdf'
    create_pdf(str(p1), ['Anti-Ragging Committee', 'Affidavit'])

    p2 = tmp_path / 'audited_financial_statement.pdf'
    create_pdf(str(p2), ['Audited Financial Statement', 'Auditor: XYZ'])

    # make zip
    zip_path = tmp_path / 'batch.zip'
    with zipfile.ZipFile(str(zip_path), 'w') as z:
        z.write(str(p1), arcname='anti_ragging.pdf')
        z.write(str(p2), arcname='audited_financial_statement.pdf')

    with open(zip_path, 'rb') as f:
        resp = client.post('/api/validate-required', files={'zipfile_upload': ('batch.zip', f, 'application/zip')})

    assert resp.status_code == 200
    # Should return CSV bytes
    assert 'text/csv' in resp.headers['content-type']
    body = resp.content.decode('utf-8')
    assert 'Anti-Ragging Committee Certificate' in body
