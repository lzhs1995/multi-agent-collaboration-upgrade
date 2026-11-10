"""Real Unix ingress/runtime/service/ledger; deployment/RPC are fixture doubles.

No experiment, native client or model is launched. Callback admission has its
own predicate suite; its observe result is explicitly mocked in these endpoint
tests, which exercise serial dispatch and the real one-shot service transition.
"""
import json
import socket
import threading
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, canonical, file_ref, json_ref, write_json
from upgrade_task_callback_ingress import REQUEST_SCHEMA, CallbackIngress
from upgrade_task_runtime import CampaignRuntime
import test_upgrade_task_callback_turn as service_fixture


class IngressRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f=service_fixture.ServiceTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.runtime=CampaignRuntime(self.f.service)
        # The base local-socket fixture's server is this test process. Keep its
        # original process reader so owner and peer observations remain equal.
        self.endpoint=self.runtime.enable_callback_ingress();self.addCleanup(self.endpoint.close)
        self.client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.addCleanup(self.client.close)
        self.client.settimeout(1);self.client.connect(str(self.endpoint.path))

    def send(self,value=None):
        request=dict(schema=REQUEST_SCHEMA,**self.f.request) if value is None else value
        self.client.sendall(canonical(request))

    def receive(self):
        data=b''
        while b'\n' not in data:
            block=self.client.recv(4096)
            self.assertTrue(block);data+=block
        return json.loads(data)

    def run_runtime(self,**kwargs):
        return self.runtime.run(seconds=.2,max_pulses=1,**kwargs)

    def test_callback_has_priority_and_uses_same_single_turn_budget(self):
        self.send();ledger=self.runtime.ledger.identity
        result=self.run_runtime()
        response=self.receive()
        self.assertEqual(response['status'],'ENROLLED')
        self.assertEqual(result['model_inputs_sent'],1)
        self.assertEqual(result['continuations'],[])
        self.assertEqual(len(result['callback_inputs']),1)
        self.assertEqual(self.runtime.ledger.identity,ledger)
        self.assertEqual(self.f.f.inputs,[self.f.initial['request']['params']])
        self.assertFalse(response['native_user_receipt'])
        self.assertFalse(response['retry_allowed'])
        self.assertEqual(json_ref(response['evidence'])['result'],response['result'])
        self.assertFalse(list(self.runtime.actor.directory.glob('*-next-input-plan.json')))

    def test_zero_budget_does_not_accept_then_same_runtime_accepts_once(self):
        self.send();first=self.run_runtime(max_new_turns=0)
        self.assertEqual(first['callback_inputs'],[])
        self.assertEqual(self.endpoint.sequence,0);self.assertFalse(self.f.f.inputs)
        self.assertEqual(self.run_runtime()['model_inputs_sent'],1)
        self.assertEqual(self.receive()['status'],'ENROLLED')

    def test_request_cannot_smuggle_arbitrary_text_or_rpc(self):
        self.send(dict(schema=REQUEST_SCHEMA,**self.f.request,text='execute arbitrary command'))
        result=self.run_runtime();response=self.receive()
        self.assertEqual(response['status'],'REFUSED_BEFORE_SERVICE')
        self.assertEqual(result['model_inputs_sent'],0)
        self.assertFalse(self.f.f.inputs)
        self.assertFalse(list(self.runtime.actor.directory.glob('*-next-turn-intent.json')))

    def test_partial_frame_is_bounded_and_does_not_trigger_input(self):
        self.client.sendall(b'{"schema":')
        result=self.run_runtime();response=self.receive()
        self.assertEqual(result['model_inputs_sent'],0)
        self.assertEqual(response['status'],'REFUSED_BEFORE_SERVICE')
        self.assertFalse(self.f.f.inputs)

    def test_endpoint_identity_change_refuses_before_accept(self):
        self.send();self.endpoint.path.chmod(0o666)
        with self.assertRaisesRegex(ContractError,'SOCKET_CHANGED'):self.run_runtime()
        self.assertTrue(self.runtime.closed);self.assertTrue(self.endpoint.closed)
        self.assertFalse(self.f.f.inputs)

    def test_unknown_model_send_closes_original_runtime_and_never_replays(self):
        self.f.f.failure=TimeoutError('fixture unknown callback input')
        self.send()
        with self.assertRaisesRegex(TimeoutError,'unknown callback input'):self.run_runtime()
        self.assertEqual(self.receive()['status'],'UNKNOWN_NO_REPLAY')
        self.assertTrue(self.runtime.closed);self.assertTrue(self.endpoint.closed)
        with self.assertRaisesRegex(ContractError,'CLOSED_NO_REPLAY'):self.run_runtime()
        self.assertEqual(len(self.f.f.inputs),1)
        self.assertTrue(self.endpoint.path.exists())
        self.assertTrue((self.endpoint.root/'000001/failure.json').exists())

    def test_second_endpoint_or_unowned_poll_never_accept(self):
        with self.assertRaisesRegex(ContractError,'ORIGINAL_IDLE_OWNER_REQUIRED'):
            CallbackIngress(self.runtime)
        with self.assertRaisesRegex(ContractError,'RUNTIME_DISPATCH_REQUIRED'):
            self.endpoint.poll()
        self.assertEqual(self.endpoint.sequence,0)
        self.assertFalse(self.f.f.inputs)

    def test_ready_service_cannot_borrow_ingress_context_outside_dispatch(self):
        from upgrade_task_callback_ingress import active_context
        self.assertFalse(active_context(self.f.service,self.client,self.f.request))
        self.assertEqual(file_ref(self.endpoint.root/'descriptor.json'),self.endpoint.proof)
        self.assertFalse(self.f.f.inputs)


class ClientRuntimeTests(unittest.TestCase):
    def setUp(self):
        f=IngressRuntimeTests();f.setUp();self.addCleanup(f.doCleanups);self.f=f
        # The first fixture connection is unused; close it before polling.
        # Accept and discard only this known test connection, before requests.
        f.client.close();connection,_=f.endpoint.listener.accept();connection.close()
        gate=f.runtime.gate
        self.binding=dict(task_target=dict(startup_result=write_json(f.f.f.f.control/'client-startup-result.json',
                            dict(operations=gate.campaign['task_controls']['registration'])),
                            startup=gate.runtime['startup']))
        p=patch('upgrade_task_target.validate_saved',return_value=(gate.spec,gate.anchor))
        p.start();self.addCleanup(p.stop)
        self.events=[];self.ready=threading.Event();self.finished=threading.Event()
        self.output={};self.thread=None

    def start(self,**kwargs):
        import upgrade_task_callback_client as client
        def record(phase,value):
            self.events.append((phase,value))
            if phase=='CALLBACK_INGRESS_SEND_INTENT':self.ready.set()
        def run():
            try:self.output['result']=client.submit_once(self.binding,**self.f.f.request,
                    process=self.f.runtime.client.process,recheck=lambda:None,record=record,timeout=2,**kwargs)
            except BaseException as exc:self.output['error']=exc
            finally:self.finished.set()
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
        self.addCleanup(self.thread.join,3)

    def test_actual_client_and_ingress_exchange_preserve_exact_evidence(self):
        self.start();self.assertTrue(self.ready.wait(2),self.output)
        result=self.f.run_runtime();self.assertTrue(self.finished.wait(2),self.output)
        self.assertNotIn('error',self.output)
        response=self.output['result'];self.assertEqual(response['status'],'ENROLLED')
        self.assertEqual(result['model_inputs_sent'],1)
        self.assertEqual([p for p,v in self.events],['CALLBACK_INGRESS_SEND_INTENT','CALLBACK_INGRESS_RESPONSE'])
        self.assertEqual(len(self.f.f.f.inputs),1)

    def test_original_descriptor_mismatch_refuses_before_network_input(self):
        import upgrade_task_callback_client as client
        path=self.f.endpoint.root/'descriptor.json'
        value=json_ref(file_ref(path));value['task_thread_id']='foreign';write_json(path,value)
        with self.assertRaisesRegex(ContractError,'ORIGINAL_DESCRIPTOR_REQUIRED'):
            client.descriptor(self.binding)
        self.assertFalse(self.events);self.assertFalse(self.f.f.f.inputs)

    def test_lost_response_after_input_is_unknown_and_no_second_send(self):
        self.f.f.f.failure=TimeoutError('fixture input outcome unknown')
        self.start();self.assertTrue(self.ready.wait(2),self.output)
        with self.assertRaisesRegex(TimeoutError,'outcome unknown'):self.f.run_runtime()
        self.assertTrue(self.finished.wait(2),self.output)
        self.assertIsInstance(self.output.get('error'),ContractError)
        self.assertIn('NOT_ENROLLED_NO_REPLAY',str(self.output['error']))
        self.assertEqual(sum(p=='CALLBACK_INGRESS_SEND_INTENT' for p,v in self.events),1)
        self.assertEqual(len(self.f.f.f.inputs),1)
        self.assertTrue(self.f.runtime.closed)


class AdmissionRuntimeTests(unittest.TestCase):
    def test_runtime_dispatch_uses_real_service_admission_and_exact_context(self):
        f=service_fixture.ObservationTests();f.setUp();self.addCleanup(f.doCleanups)
        runtime=CampaignRuntime(f.f.service)
        endpoint=runtime.enable_callback_ingress();self.addCleanup(endpoint.close)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.connect(str(endpoint.path))
            client.sendall(canonical(dict(schema=REQUEST_SCHEMA,**f.request)))
            result=runtime.run(seconds=.2,max_pulses=1)
            self.assertEqual(result['model_inputs_sent'],1)
            self.assertEqual(f.f.inputs,[f.params['params']])
            self.assertEqual(json.loads(client.recv(32768))['status'],'ENROLLED')
        self.assertEqual(runtime.ledger.active_turn,f.f.turn)


if __name__=='__main__':unittest.main()
