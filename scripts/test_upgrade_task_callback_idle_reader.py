"""Original .84 reader/publisher and campaign reader require idle provenance.

The service result verifier is a spy here: its real snapshot/SQLite checks are
covered in test_upgrade_task_callback_route. Deployment/receiver authorization
use the existing explicit settlement fixture. This is not a live delivery test.
"""
import copy
import json
import time
from pathlib import Path
from unittest.mock import patch
import unittest

from orchestration_common import ContractError,file_ref,utc_now,write_json
from orchestration_native import verify_delivery
import test_upgrade_task_callback as fixture
import upgrade_task_callback_route as route


class IdleReaderTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.SettlementTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f
        endpoint=dict(task_thread_id=f.bound['session_id'])
        ref=write_json(f.root/'ingress.json',endpoint)
        p=patch('upgrade_task_callback_contract.historical_descriptor',return_value=(ref,endpoint))
        p.start();self.addCleanup(p.stop)
        request=copy.deepcopy(f.attempt['events'][0]['native_request'])
        request.update(method='turn/start',observed_status='idle',task_callback_route=dict(
            schema=route.SCHEMA,ingress=ref,pack=f.pack_ref,attempt=str(f.attempt_path)))
        request['params'].pop('expectedTurnId')
        request['native_metadata'].update(thread_status='idle',latest_turn_status='completed',
                                          available_input_method='turn/start')
        self.request=request
        fields=f.f.native.input_event_fields(f.bound,f.text,'NATIVE_INPUT_INTENT',dict(native_request=request))
        f.attempt['events']=[dict(phase='NATIVE_INPUT_INTENT',at_epoch=time.time(),**fields)]
        write_json(f.attempt_path,f.attempt)
        f.raw=(json.dumps(dict(timestamp=utc_now(),type='response_item',payload=dict(
            type='message',role='user',content=[dict(type='input_text',text=f.text)])))+'\n').encode()
        f.line=len(f.f.f.path.read_bytes().splitlines())+1
        with f.f.f.path.open('ab') as stream:stream.write(f.raw)
        p=patch.object(route,'verify_input',return_value={'fixture':'original service result'})
        self.verify=p.start();self.addCleanup(p.stop)

    def read(self,receipt):
        f=self.f
        return verify_delivery(dict(receipt=write_json(f.root/'readback.json',receipt),
            attempts=[file_ref(f.attempt_path)]),f.receiver,f.sender,f.text,
            fixtures=True,uuid_semantics=True)

    def test_original_receiver_publication_and_campaign_reader_use_same_route(self):
        f=self.f;before=file_ref(f.attempt_path)
        receipt=f.settle()
        self.assertGreater(self.verify.call_count,0)
        self.verify.reset_mock()
        self.assertEqual(self.read(receipt)['transport_retries'],0)
        self.assertGreater(self.verify.call_count,0)
        for call in self.verify.call_args_list:
            self.assertEqual(call.args,(f.bound,self.request,f.attempt))
        self.assertEqual(file_ref(f.attempt_path),before);self.assertFalse(f.f.inputs)

    def test_missing_service_provenance_refuses_publication_and_campaign_acceptance(self):
        self.verify.side_effect=ContractError('TASK_CALLBACK_ROUTE_ORIGINAL_TURN_NOT_ENROLLED')
        with self.assertRaises((ContractError,self.f.receipts.ReceiptError)):
            self.f.settle()
        self.assertFalse(self.f.receipt.exists());self.assertFalse(self.f.f.inputs)
        self.verify.side_effect=None
        receipt=self.f.settle()
        self.verify.side_effect=ContractError('TASK_CALLBACK_ROUTE_ORIGINAL_TURN_NOT_ENROLLED')
        with self.assertRaisesRegex(ContractError,'ORIGINAL_TURN_NOT_ENROLLED'):self.read(receipt)


if __name__=='__main__':unittest.main()
