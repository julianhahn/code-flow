"""Bounded overview text, without generated claims about the code."""
import re


def checks_summary(checks):
    if not checks:
        return 'Checks: none reported'
    counts = dict(failed=0, pending=0, skipped=0, passed=0, unknown=0)
    for check in checks:
        state = check.get('conclusion') or check.get('state') or check.get('status') or ''
        if state in ('FAILURE', 'ERROR', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STALE', 'STARTUP_FAILURE'):
            counts['failed'] += 1
        elif state in ('QUEUED', 'IN_PROGRESS', 'PENDING', 'WAITING', 'REQUESTED', 'EXPECTED'):
            counts['pending'] += 1
        elif state in ('SKIPPED', 'NEUTRAL'):
            counts['skipped'] += 1
        elif state == 'SUCCESS':
            counts['passed'] += 1
        else:
            counts['unknown'] += 1
    parts = [f"{counts['failed']} failed" if counts['failed'] else 'no failures']
    for key in ('pending', 'skipped', 'unknown'):
        if counts[key]:
            parts.append(f'{counts[key]} {key}')
    return 'Checks: ' + ' · '.join(parts)


def purpose_excerpt(body):
    paragraphs = re.split(r'\n\s*\n', (body or '').replace('\r\n', '\n'))
    for paragraph in paragraphs:
        text = paragraph.strip()
        if not text or text.startswith(('#', '>', '<', '---', '```', '|', '- [', '* [')):
            continue
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        text = re.sub(r'[`*_]', '', text)
        text = ' '.join(text.split())
        if len(text) > 360:
            text = text[:357].rsplit(' ', 1)[0] + '…'
        return text
    return 'No short description available. Read the full description on GitHub.'
