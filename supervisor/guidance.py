"""Instruction identity binds a task to the operator's current intent."""
from .safety import digest


def snapshot(store):
    value = store.get('ralph_instructions', '')
    text = value.strip() if isinstance(value, str) else ''
    return {'text': text, 'fingerprint': digest(text.encode('utf-8')),
            'has_instructions': bool(text)}


def matches(proposal, guidance):
    # Legacy tasks were discovered with no custom operator guidance.
    expected = proposal.get('instructions_fingerprint', digest(b''))
    return expected == guidance['fingerprint']
