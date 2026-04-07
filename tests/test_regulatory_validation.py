import os
import tempfile
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from final_certificate_verification import CertificateVerifier


def create_pdf_with_text(path: str, lines):
    c = canvas.Canvas(path, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()


def test_aicte_validation_positive(tmp_path):
    # Create a sample AICTE certificate containing approval number and other keywords
    p = tmp_path / "aicte_positive.pdf"
    lines = [
        'AICTE Approval No: AICTE/2025/1234',
        'This institute is affiliated and approved by AICTE',
        'Principal: Dr. A Name',
        'Course: B.Tech in Computer Science',
        'Date: 01-01-2025'
    ]
    create_pdf_with_text(str(p), lines)

    v = CertificateVerifier()
    res = v.process_certificate(str(p), ['Pune', 'Institute'], interactive=False, regulatory='AICTE')

    assert res['regulatory_check'] is not None
    rc = res['regulatory_check']
    assert rc['regulatory'] == 'AICTE'
    assert rc['pass'] is True
    assert rc['approval']['valid'] is True
    assert 'aicte' in rc['element_matches']


def test_aicte_validation_negative(tmp_path):
    # Create a sample that lacks AICTE approval number
    p = tmp_path / "aicte_negative.pdf"
    lines = [
        'Some institute certificate',
        'This institute is affiliated',
        'Principal: Dr. B Name',
        'Course: Diploma in Arts',
        'Date: 01-01-2025'
    ]
    create_pdf_with_text(str(p), lines)

    v = CertificateVerifier()
    res = v.process_certificate(str(p), ['Pune', 'Institute'], interactive=False, regulatory='AICTE')

    assert res['regulatory_check'] is not None
    rc = res['regulatory_check']
    assert rc['regulatory'] == 'AICTE'
    # Should not pass since no approval number and fewer keywords
    assert rc['pass'] is False
    assert rc['approval']['valid'] is False
