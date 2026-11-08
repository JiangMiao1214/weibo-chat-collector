import unittest
import test_quote_context_http as fixtures


class ContextRecoveryTests(unittest.TestCase):
    insert = fixtures.QuoteContextHttpTests.insert
    lookup = fixtures.QuoteContextHttpTests.lookup

    def setUp(self):
        fixtures.QuoteContextHttpTests.setUp(self)
        self.db.execute("UPDATE chat_groups SET source_group_id='fake-group' WHERE id=11")
        self.db.commit()

    def request(self, **extra):
        return self.client.post('/api/messages/10/recover-context', json={
            'account_id': 1, 'group_id': 11, 'range_start': '2026-08-22 00:00:00',
            'range_end': '2026-08-23 10:00:00', 'confirmed': True, **extra})

    def test_local_match_blocks_unnecessary_network_task(self):
        response = self.request()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM collection_jobs').fetchone()[0], 0)

    def test_missing_original_creates_bounded_unconfirmed_queue_without_network(self):
        self.db.execute("UPDATE messages SET content_text='不同正文' WHERE id=1")
        response = self.request()
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(job['max_pages'], 20)
        self.assertIsNone(self.db.execute('SELECT confirmed_at FROM collection_jobs').fetchone()[0])
        self.assertEqual(self.request().status_code, 409)

    def test_recovery_scope_and_time_are_bounded(self):
        self.assertEqual(self.request(account_id=2).status_code, 404)
        self.assertEqual(self.request(confirmed=False).status_code, 422)
        self.assertEqual(self.request(range_start='2026-01-01 00:00:00').status_code, 422)
        self.assertEqual(self.request(range_end='2026-08-24 00:00:00').status_code, 422)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM collection_jobs').fetchone()[0], 0)

class RecoveryLimitWorkerTests(unittest.TestCase):
    def test_worker_stops_at_recovery_cap_and_preserves_next_cursor(self):
        from test_collection_worker import CollectionWorkerIntegrationTests as Fixture, FakePageClient, raw_message
        fixture=Fixture('test_page_transaction_filters_system_noise_but_keeps_greeting')
        fixture.setUp()
        try:
            original_cap=fixture.settings.weibo_api_max_pages
            job=fixture.create_confirmed_job()
            fixture.connection.execute('UPDATE collection_jobs SET recovery_max_pages=1 WHERE id=?',(job,))
            fixture.connection.commit()
            client=FakePageClient({'0':[{'messages':[raw_message('1000',10,'首个模拟页')]}]})
            fixture.worker_for(client).run_once()
            state=fixture.job(job)
            self.assertEqual(state['stop_code'],'page_limit')
            self.assertEqual(state['page_count'],1)
            self.assertEqual(state['next_max_mid'],'1000')
            self.assertEqual(fixture.settings.weibo_api_max_pages,original_cap)
        finally:
            fixture.tearDown()

class RecoveryCooldownTests(unittest.TestCase):
    setUp=ContextRecoveryTests.setUp
    insert=ContextRecoveryTests.insert
    request=ContextRecoveryTests.request
    def test_new_recovery_cannot_bypass_existing_account_risk_cooldown(self):
        self.db.execute("UPDATE messages SET content_text='无匹配' WHERE id=1")
        self.db.execute("""INSERT INTO collection_jobs(account_id,group_id,range_start,range_end,status,collector_type,resume_not_before)
            VALUES(1,11,'2026-08-01','2026-08-02','stopped','weibo_api_v2','2099-01-01 00:00:00')""")
        response=self.request()
        self.assertEqual(response.status_code,409,response.text)
        self.assertIn('冷却',response.text)
