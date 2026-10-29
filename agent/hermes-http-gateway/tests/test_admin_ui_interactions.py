import shutil
import subprocess
import unittest
from pathlib import Path
from hermes_http_gateway.admin_ui import render_dashboard_page


class AdminUIInteractionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for admin JavaScript checks')
    def test_rendered_script_interactions(self):
        page = render_dashboard_page(username='admin', csrf_token='test')
        script = page.split('<script>')[1].split('</script>')[0]
        result = subprocess.run(['node', str(Path(__file__).with_name('admin_ui.test.cjs'))],
                                input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
