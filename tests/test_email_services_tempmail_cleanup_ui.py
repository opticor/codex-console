from pathlib import Path


def test_email_services_template_exposes_tempmail_cleanup_checkbox_only_for_tempmail_section():
    template = Path("templates/email_services.html").read_text(encoding="utf-8")

    assert 'id="add-tempmail-cleanup-group"' in template
    assert 'id="edit-tempmail-cleanup-group"' in template
    assert 'id="custom-tm-cleanup-on-failure"' in template
    assert 'id="edit-tm-cleanup-on-failure"' in template


def test_email_services_js_only_submits_cleanup_flag_for_tempmail():
    script = Path("static/js/email_services.js").read_text(encoding="utf-8")

    assert "elements.addTempmailCleanupGroup.style.display = subType === 'tempmail' ? '' : 'none';" in script
    assert "elements.editTempmailCleanupGroup.style.display = subType === 'tempmail' ? '' : 'none';" in script
    assert "if (subType === 'tempmail') {" in script
    assert "config.cleanup_on_task_failure = formData.get('cleanup_on_task_failure') === 'on';" in script
    assert "resolvedSubType === 'tempmail'" in script
