import io
import unittest
from PIL import Image
import test_archives_http as fixtures
from app.services.archive_storage import save_stream


class ArchiveThumbnailTests(unittest.TestCase):
    setUp = fixtures.ArchivesHttpTests.setUp
    restore_overrides = staticmethod(fixtures.ArchivesHttpTests.restore_overrides)

    def test_thumbnail_is_small_cached_scoped_and_never_changes_original(self):
        output = io.BytesIO()
        Image.new('RGB', (1800, 1200), 'blue').save(output, format='PNG')
        body = output.getvalue()
        result = save_stream(self.settings.attachments_dir, 1, [body], 'image/png')
        self.db.execute("INSERT INTO archive_jobs(id,message_id,source_url,kind,status,local_path,content_hash,mime_type) VALUES(70,1,'https://fixture.invalid/p.png','image','stored',?,?,'image/png')", (result['local_path'], result['content_hash']))
        self.db.commit()
        url = '/api/archives/70/thumbnail?account_id=1&group_id=11'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        with Image.open(io.BytesIO(response.content)) as thumb:
            self.assertLessEqual(max(thumb.size), 320)
        self.assertEqual(self.client.get(url.replace('account_id=1', 'account_id=2')).status_code, 404)
        self.assertEqual((self.settings.attachments_dir / result['local_path']).read_bytes(), body)
        self.assertEqual(self.client.get(url).content, response.content)
