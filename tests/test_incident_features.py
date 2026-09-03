import json
import os
from pathlib import Path


def _case():
    return json.loads(Path('apps/web/demo/case-demo.json').read_text(encoding='utf-8'))


def test_incident_pdf_generation(tmp_path, monkeypatch):
    from spilltrace_api import reports

    monkeypatch.setattr(reports, 'REPORT_DIR', tmp_path)
    path = reports.generate_incident_report(_case(), case_number='TEST-001')
    assert path.name == 'TEST-001_incident_report.pdf'
    assert path.read_bytes().startswith(b'%PDF')


def test_dispatch_dry_run(tmp_path, monkeypatch):
    from spilltrace_api import dispatch
    from spilltrace_api import reports

    monkeypatch.setattr(reports, 'REPORT_DIR', tmp_path)
    monkeypatch.setattr(dispatch, 'generate_incident_report', reports.generate_incident_report)
    monkeypatch.setenv('SPILLTRACE_EMAIL_DRY_RUN', '1')
    monkeypatch.setenv('SPILLTRACE_EMAIL_FROM', 'sender@example.com')

    result = dispatch.dispatch_case_email(_case(), ['receiver@example.com'], case_number='TEST-002')
    assert result['dryRun'] is True
    assert result['sent'] is False
    assert Path(result['reportPath']).exists()
    assert Path(result['emailPath']).exists()
