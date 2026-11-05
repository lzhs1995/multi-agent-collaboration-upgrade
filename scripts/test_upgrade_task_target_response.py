"""S40 response-incarnation regressions; local socket, simulated RPC/process."""
import unittest
from unittest.mock import patch

from orchestration_common import ContractError
from test_upgrade_task_target import TargetTests


class ResponsePeerTests(unittest.TestCase):
    def setUp(self):
        self.f = TargetTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.f.thread['status']['type'] = 'active'
        self.f.f.turns[0]['status'] = 'inProgress'

    def metadata_case(self, reader, changed_method):
        f = self.f; bound = f.bind(); original = f.RPC.request; calls = []
        def switched(client, method, params):
            calls.append(method)
            result = original(client, method, params)
            if method == changed_method:
                f.f.row['birth'] = [f.f.row['birth'][0] + 1, f.f.row['birth'][1]]
                # Prove the peer check also precedes indexing response fields.
                return None
            return result
        with patch.object(f.RPC, 'request', switched):
            with self.assertRaisesRegex(ContractError, 'PEER|SERVER'):
                reader(f.RPC(), bound, 'exact callback body')
        self.assertEqual(calls[-1], changed_method)
        self.assertFalse(f.inputs)

    def test_metadata_first_response_rechecked_before_second_read(self):
        self.metadata_case(self.f.input.read_metadata, 'thread/read')

    def test_metadata_last_response_rechecked_before_use(self):
        self.metadata_case(self.f.input.read_metadata, 'thread/turns/list')

    def test_outcomes_first_response_rechecked_before_second_read(self):
        self.metadata_case(self.f.input.read_terminal_outcomes, 'thread/read')

    def test_outcomes_last_response_rechecked_before_use(self):
        self.metadata_case(self.f.input.read_terminal_outcomes, 'thread/turns/list')

    def test_mutation_changed_peer_preserves_unknown_single_intent(self):
        f = self.f; bound = f.bind(); original = f.RPC.request
        def switched(client, method, params):
            result = original(client, method, params)
            if method == 'turn/steer':
                f.f.row['birth'] = [f.f.row['birth'][0] + 1, f.f.row['birth'][1]]
            return result
        with patch.object(f.RPC, 'request', switched):
            result = f.input.send_once(bound, 'exact callback body', recheck=lambda: None,
                emit=lambda phase, **kw: f.events.append((phase, kw)),
                observe=lambda: dict(confirmed=False), wait_seconds=0,
                fallback=lambda: self.fail('no fallback after original intent'))
        self.assertEqual(len(f.inputs), 1)
        self.assertEqual(result['native_rpc_outcome'], 'RPC_ERROR_OR_UNKNOWN')
        self.assertIsNone(result['native_turn_id'])
        self.assertEqual(result['input_operations'], 1)
        self.assertEqual(result['retries'], 0)
        self.assertFalse(result['confirmed'])
        self.assertEqual([p for p, _ in f.events], ['NATIVE_INPUT_INTENT', 'NATIVE_INPUT_RESULT'])


if __name__ == '__main__': unittest.main()
