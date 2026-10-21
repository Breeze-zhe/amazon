from datetime import date
import unittest
from unittest.mock import Mock, patch

import crawl_site_sequence as seq


class SequenceTests(unittest.TestCase):
    def run_sites(self, jobs, auth=False):
        response = Mock()
        response.json.return_value = {'job': {'id': 'test-job'}}
        session = Mock()
        session.post.return_value = response
        state = {'count_day': date.today().isoformat(), 'results': {}}
        def publish(state, **values):
            state.update(values)
        with patch.object(seq, 'publish', side_effect=publish), \
             patch.object(seq, 'page_requires_auth', return_value=auth), \
             patch.object(seq, 'wait_job', side_effect=jobs):
            seq.execute_sequence(session, 'http://127.0.0.1:6471', {'J': [174], 'A': [2], 'U': [3]}, state)
        return session, state

    def test_strict_japan_australia_usa_order(self):
        job = {'status': 'finished', 'result': {'complete': True, 'remaining_re_pull': 0}}
        session, state = self.run_sites([job, job, job])
        self.assertEqual([call.kwargs['json']['data_ids'] for call in session.post.call_args_list], [[174], [2], [3]])
        self.assertEqual([call.kwargs['json']['debugger_address'] for call in session.post.call_args_list],
                         ['127.0.0.1:9230', '127.0.0.1:9231', '127.0.0.1:9232'])
        self.assertEqual(state['status'], 'finished')
        self.assertTrue(state['complete'])
        for call in session.post.call_args_list:
            payload = call.kwargs['json']
            self.assertEqual(payload['workers'], 1)
            self.assertFalse(payload['resume'])
            self.assertTrue(payload['skip_deal'])
            self.assertTrue(payload['exclude_activity'])

    def test_manual_auth_stops_before_any_submission(self):
        session, state = self.run_sites([], auth=True)
        session.post.assert_not_called()
        self.assertEqual(state['status'], 'authentication_required')
        self.assertEqual(state['site'], 'J')

    def test_pause_does_not_start_next_market(self):
        session, state = self.run_sites([{'status': 'paused'}])
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(state['status'], 'paused')

    def test_interruption_does_not_start_next_market(self):
        session, state = self.run_sites([{'status': 'interrupted'}])
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(state['status'], 'paused')

    def test_bounded_incomplete_results_are_not_reported_complete(self):
        job = {'status': 'finished', 'result': {'complete': False, 'remaining_re_pull': 1}}
        session, state = self.run_sites([job, job, job])
        self.assertEqual(session.post.call_count, 3)
        self.assertFalse(state['complete'])
        self.assertTrue(all(r['remaining'] == 1 for r in state['results'].values()))


if __name__ == '__main__':
    unittest.main()
