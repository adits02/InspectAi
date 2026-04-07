import os
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from final_certificate_verification import CertificateVerifier


def create_pdf(path, lines):
    c = canvas.Canvas(path, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.save()


def test_validate_required_certificates_tmpdir(tmp_path):
    # Create a few sample PDFs that correspond to some required certificates
    cert1 = tmp_path / 'anti_ragging.pdf'
    create_pdf(str(cert1), ['Anti-Ragging Committee', 'This is to certify that', 'Name: John Doe'])

    cert2 = tmp_path / 'aicte_approval.pdf'
    create_pdf(str(cert2), ['AICTE Approval No: AICTE/2025/999', 'Approved Institute'])

    cert3 = tmp_path / 'audited_financial_statement.pdf'
    create_pdf(str(cert3), ['Audited Financial Statement', 'Auditor: XYZ'])

    v = CertificateVerifier()
    report = v.validate_required_certificates_in_dir(str(tmp_path))

    # Check that expected keys exist
    assert 'Anti-Ragging Committee Certificate' in report
    assert 'Audited Financial Statement' in report

    # The anti-ragging file should be found and have at least one match
    ar = report['Anti-Ragging Committee Certificate']
    assert ar['found'] is True
    assert len(ar['matches']) >= 1

    af = report['Audited Financial Statement']
    assert af['found'] is True
    assert any('audited' in m['validation']['element_matches'] for m in af['matches'])
