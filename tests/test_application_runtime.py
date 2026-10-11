import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import application_runtime as app
from supervisor.store import Store


class ApplicationRevisionTests(unittest.TestCase):
    def test_only_complete_matching_source_is_reloadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            runtime = root / 'runtime'
            runtime.mkdir()
            source = root / 'src'
            source.mkdir()
            code = source / 'example.py'
            code.write_bytes(b'original')
            store = Store(runtime / 'state.sqlite')
            store.set('source', str(root))
            store.set('source_manifest', {'src/example.py': hashlib.sha256(b'original').hexdigest()})
            with patch.object(app, 'PROJECT', root), patch.object(app, 'RUNTIME', runtime):
                baseline = app.validated_revision()
                self.assertIsNotNone(baseline)
                store.set('pending_promotion', 'journal')
                self.assertIsNone(app.validated_revision())
                code.write_bytes(b'changed')
                store.set('pending_promotion', None)
                self.assertIsNone(app.validated_revision())
                store.set('source_manifest', {'src/example.py': hashlib.sha256(b'changed').hexdigest()})
                self.assertNotEqual(baseline, app.validated_revision())
                store.set('source', str(runtime))
                self.assertIsNone(app.validated_revision())
            store.close()
