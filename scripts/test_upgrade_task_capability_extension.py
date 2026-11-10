"""Offline native-format integration; no live clients, model calls or inputs."""
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from orchestration_common import ContractError, canonical, digest, file_ref, object_sha, write_json
from orchestration_native import NativeReader as RealReader
from upgrade_task_operations import OperationLedger
import upgrade_task_capability_extension as ext
import upgrade_task_inquiry_capability as inquiry
from upgrade_task_capability_service import CapabilityService


class CapabilityFixture:
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve();self.root.chmod(0o700)
        self.turn=str(uuid.uuid4());self.previous=str(uuid.uuid4())
        self.server=dict(pid=3456,birth=[123456,1],uid=os.geteuid(),executable='/fixture/codex',
            file_identity=[1,2,3,4],argv_sha256='a'*64)
        self.sessions={}; self.workspace=str(uuid.uuid4()).upper()
        for lane in ('codex','claude_a','claude_b'):
            sid=str(uuid.uuid4());path=self.root/(lane+'-'+sid+'.jsonl')
            path.write_bytes(canonical(dict(type='session_meta',payload=dict(id=sid))) if lane=='codex'
                else canonical(dict(type='system',sessionId=sid)))
            st=path.stat();self.sessions[lane]=dict(provider='codex' if lane=='codex' else 'claude',
                session_id=sid,surface_uuid=str(uuid.uuid4()),workspace_uuid=self.workspace,
                transcript=dict(path=str(path),device=st.st_dev,inode=st.st_ino),
                freeze_fence=dict(offset=path.stat().st_size))
        self.authority=self.save('original-authority.json',dict(fixture='original task authority'))
        self.budget=self.save('budget.json',dict(max_children=2,scope='WHOLE_TASK'))
        self.runtime=self.save('runtime.json',dict(state_root=str(self.root/'state')))
        self.adoption=self.save('adoption.json',dict(sessions=self.sessions))
        self.base=dict(schema='upgrade-task-operations-v3',task_id='fixture-task',
            thread_id=self.sessions['codex']['session_id'],turn_id=self.previous,
            controller_id='original-controller',authority_ref=self.authority,
            operations={'ready':dict(kind='readiness',payload={}),
                        'accept':dict(kind='campaign-control',payload={'marker':None})},server=self.server,
            initial_operations=['ready'],choice_groups={},binding_slots={'accept':dict(
                schema='upgrade-task-artifact-slot-v1',resolver='campaign-accept-marker-v1',
                source_path=str(self.root/'future-candidate.json'),producer_operation='ready')})
        self.base_ref=self.save('operations.json',self.base)
        self.ledger=OperationLedger.create(self.root/'ledger.sqlite',self.base);self.addCleanup(self.ledger.close)
        st=self.ledger.path.stat(); self.ledger_pin=dict(path=str(self.ledger.path),device=st.st_dev,inode=st.st_ino)
        self.completion=self.save('closed.json',dict(direction='received',peer=self.peer(),
            message=dict(method='turn/completed',params=dict(threadId=self.base['thread_id'],
                         turn=dict(id=self.previous,status='completed')))))
        self.request=dict(schema=ext.REQUEST_SCHEMA,extension_id='fixture-extension',
            base_registration=self.base_ref,base_ledger=self.ledger_pin,runtime=self.runtime,
            adoption=self.adoption,previous_completion=self.completion,operations={})
        self.payload=self.make_episode()
        self.request['operations']={'read':dict(action='inquiry-read',payload=self.payload),
            'answer':dict(action='inquiry-answer',payload=dict(self.payload,read_operation_id='read'))}
        self.request_ref=self.accept_request()
        self.runtime_patch=patch.object(ext,'_runtime_binding',side_effect=lambda request:(
            json.loads(Path(self.runtime['path']).read_text()),{},copy.deepcopy(self.sessions),dict(server=self.server)))
        self.runtime_patch.start();self.addCleanup(self.runtime_patch.stop)
        self.reader_patch=patch.object(ext,'NativeReader',side_effect=lambda session:RealReader(session,fixtures=True))
        self.reader_patch.start();self.addCleanup(self.reader_patch.stop)
        self.service_reader_patch=patch('orchestration_native.NativeReader',side_effect=lambda session:RealReader(session,fixtures=True))
        self.service_reader_patch.start();self.addCleanup(self.service_reader_patch.stop)
        self.user_patch=patch('orchestration_freeze_v3.authorize',side_effect=self.native_auth)
        self.user_patch.start();self.addCleanup(self.user_patch.stop)
        self.publisher_patch=patch('upgrade_live_control.external_publisher')
        self.publisher_patch.start();self.addCleanup(self.publisher_patch.stop)
        self.peer_patch=patch('upgrade_rpc_peer.verify_peer',return_value=self.peer())
        self.peer_patch.start();self.addCleanup(self.peer_patch.stop)

    def save(self,name,value):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(canonical(value));return file_ref(p)

    def peer(self):return dict(schema='upgrade-task-rpc-peer-v1',server=self.server,peer={k:self.server[k] for k in ('pid','uid')})

    def append(self,lane,payload,kind='response_item'):
        path=Path(self.sessions[lane]['transcript']['path']);offset=path.stat().st_size
        raw=canonical(dict(timestamp='2026-11-10T12:00:00Z',type=kind,payload=payload))
        with path.open('ab') as f:f.write(raw)
        return dict(**self.sessions[lane]['transcript'],offset=offset,length=len(raw),sha256=digest(raw))

    def statement(self,value,*,role='assistant',turn=None):
        return self.append('codex',dict(type='message',role=role,
            content=[dict(type='output_text' if role=='assistant' else 'input_text',text=json.dumps(value))],
            internal_chat_message_metadata_passthrough=dict(turn_id=turn or self.turn)))

    def native_auth(self,records):
        if records != [dict(fixture='native-user-scope')]:raise ContractError('test native authority rejected')
        return records

    def accept_request(self):
        source=[dict(fixture='native-user-scope')]
        acceptance=self.statement(dict(schema='upgrade-task-capability-acceptance-v1',
            task_id=self.base['task_id'],extension_id=self.request['extension_id'],
            scope_sha256=ext.scope_digest(self.request),native_authorizations_sha256=object_sha(source),
            budget_sha256=self.budget['sha256']),turn=self.previous)
        authority=dict(schema='upgrade-task-capability-authority-v1',task_id=self.base['task_id'],
            scope_sha256=ext.scope_digest(self.request),native_authorizations=source,
            owner_acceptance=acceptance,budget=self.budget)
        self.request['authority']=self.save('extension-authority.json',authority)
        return self.save('request.json',self.request)

    def make_episode(self):
        root=self.root/'state';caller=self.sessions['claude_a']['surface_uuid'].upper()
        supervisor=self.sessions['codex']['surface_uuid'].upper();marker='EXECUTOR_IDLE_fixture'
        mailbox=root/'executor-idle-v1'/'mailbox'/inquiry._hash(caller)[:16];mailbox.mkdir(parents=True)
        state_path=root/'executor-reask-v1'/(inquiry._hash(caller)+'.json')
        enrollment_path=root/'executor-reask-scheduler-v1'/'enrolled'/(inquiry._hash(caller)+'.json')
        request_path=root/'supervisor-inbox-v1'/inquiry._hash(supervisor)/(inquiry._hash(caller)+'.json')
        text='Complete original inquiry body.\nSecond line must be exposed.'
        state=dict(caller_surface_uuid=caller,supervisor=supervisor,task_id=self.base['task_id'],
            workspace_uuid=self.workspace,purpose='REQUEST_NEXT_TASK',episode_id='original-episode',channels=[],
            mailbox=str(mailbox),state='WAITING_REPLY',file_only_preference=True,
            registration=dict(token='0'*32,enrollment=str(enrollment_path)),
            continuation=dict(enabled=True,status='ACTIVE',reservation_inquiries=True,proactive_hook=True,
                              id='original-grant',overall_task='fixture-task',owner=supervisor,
                              authorized_epoch=1,until_epoch=None,controller='original-controller',
                              authorization=self.authority),asks=[dict(marker=marker,
                attempt=1,at_epoch=120,due_epoch=120,text_sha256=inquiry._hash(text),
                channels=dict(written=[str(request_path)],errors=[]),outcome='FILE_ONLY_SCHEDULED',
                terminal=False,input_operations=0)])
        self.save(str(state_path.relative_to(self.root)),state)
        keys=('caller_surface_uuid','supervisor','workspace_uuid','task_id','purpose','episode_id','channels','mailbox')
        binding={k:state[k] for k in keys}
        binding['continuation']={k:v for k,v in state['continuation'].items() if k!='status'}
        enrollment=self.save(str(enrollment_path.relative_to(self.root)),dict(schema='executor-reask-enrollment-v2',
            status='ENROLLED',uid=os.geteuid(),authorization='EXPLICIT_REASK_START',registration=state['registration'],
            binding=binding,live_binding=dict(caller_surface_uuid=caller,
            target_surface_uuid=supervisor,workspace_uuid=self.workspace)))
        request=self.save(str(request_path.relative_to(self.root)),dict(schema='executor-reask-current-v2',
            status='WAITING_REPLY',marker=marker,at_epoch=120,workspace_uuid=self.workspace,
            caller_surface_uuid=caller,supervisor=supervisor,task_id=self.base['task_id'],
            episode_id=state['episode_id'],request=text,reply_mailbox=str(mailbox/(marker+'.json'))))
        return dict(lane='claude_a',episode=dict(path=str(state_path),episode_id=state['episode_id'],
            registration_token=state['registration']['token']),enrollment=enrollment,request=request)

    def register(self):return ext.register_extension(self.request_ref)


class RegistrationTests(CapabilityFixture,unittest.TestCase):
    def test_original_due_and_subsequent_sixty_second_spacing_are_enforced(self):
        path=Path(self.payload['episode']['path']);state=json.loads(path.read_text())
        state['asks'][0]['due_epoch']=121;path.write_bytes(canonical(state))
        with self.assertRaisesRegex(ContractError,'BEFORE_ORIGINAL_DUE'):self.register()
        state['asks'][0].update(due_epoch=120,attempt=2)
        state['asks'].append(dict(marker='original_previous',attempt=1,at_epoch=90,due_epoch=90))
        path.write_bytes(canonical(state))
        with self.assertRaisesRegex(ContractError,'60_SECOND'):self.register()
        state['asks'][1]['at_epoch']=60;path.write_bytes(canonical(state));self.register()

    def test_original_channel_requires_ask_body_hash_and_written_path(self):
        path=Path(self.payload['episode']['path']);state=json.loads(path.read_text())
        state['asks'][0]['text_sha256']='0'*64;path.write_bytes(canonical(state))
        with self.assertRaisesRegex(ContractError,'ORIGINAL_FILE_CHANNEL'):self.register()
        state['asks'][0]['text_sha256']=inquiry._hash(json.loads(read_ref_text(self.payload['request']))['request'])
        state['asks'][0]['channels']['written']=[];path.write_bytes(canonical(state))
        with self.assertRaisesRegex(ContractError,'ORIGINAL_FILE_CHANNEL'):self.register()

    def test_changed_continuation_grant_cannot_borrow_old_enrollment(self):
        path=Path(self.payload['episode']['path']);state=json.loads(path.read_text())
        state['continuation']['owner']='foreign-owner';path.write_bytes(canonical(state))
        with self.assertRaisesRegex(ContractError,'COMMITTED_ENROLLMENT'):self.register()

    def test_goal_authority_uses_explicit_v2_and_binds_context(self):
        from upgrade_task_goal_authority import verify_goal_authority
        source=dict(evidence=self.save('goal-evidence.json',dict(fixture=True)),
            context=dict(original_session_id='fixture',goal_created_at=123,
                         attachment_bytes=15,attachment_sha256='f'*64))
        acceptance=self.statement(dict(schema='upgrade-task-capability-acceptance-v1',
            task_id=self.base['task_id'],extension_id=self.request['extension_id'],
            scope_sha256=ext.scope_digest(self.request),goal_authorization_sha256=object_sha(source),
            budget_sha256=self.budget['sha256']))
        authority=dict(schema='upgrade-task-capability-authority-v2',task_id=self.base['task_id'],
            scope_sha256=ext.scope_digest(self.request),goal_authorization=source,
            owner_acceptance=acceptance,budget=self.budget)
        self.request['authority']=self.save('goal-authority.json',authority)
        self.request_ref=self.save('goal-request.json',self.request)
        with patch('upgrade_task_goal_authority.verify_goal_authority',return_value=dict(native_user_message=False)) as verify:
            self.register();verify.assert_called_once_with(source['evidence'],expected_context=source['context'])
        source['context']['attachment_sha256']='0'*64
        self.request['authority']=self.save('changed-goal-authority.json',authority)
        with patch('upgrade_task_goal_authority.verify_goal_authority',return_value=dict(native_user_message=False)):
            with self.assertRaisesRegex(ContractError,'OWNER_ACCEPTANCE'):
                ext.inspect_request(self.save('changed-goal-request.json',self.request))
    def test_registration_is_inert_and_old_ledger_is_unchanged(self):
        before=ext._ledger_snapshot(self.ledger_pin,self.base);ref=self.register()
        self.assertEqual(ext.load_extension(ref)['base_snapshot'],before)
        self.assertEqual(ext._ledger_snapshot(self.ledger_pin,self.base),before)
        with self.assertRaisesRegex(ContractError,'NO_RESET'):self.register()

    def test_null_adoption_rejected_before_registering(self):
        self.runtime_patch.stop()
        self.request['adoption']=None
        with self.assertRaises(ContractError):ext.register_extension(self.save('bad-request.json',self.request))

    def test_missing_authority_is_not_inferred_from_route_budget(self):
        authority=json.loads(Path(self.request['authority']['path']).read_text());authority['native_authorizations']=[]
        self.request['authority']=self.save('bad-authority.json',authority)
        with self.assertRaises(ContractError):ext.register_extension(self.save('bad-request.json',self.request))

    def test_user_echo_cannot_be_owner_acceptance(self):
        authority=json.loads(Path(self.request['authority']['path']).read_text())
        accepted,_=ext._assistant(RealReader(self.sessions['codex'],fixtures=True),authority['owner_acceptance'])
        authority['owner_acceptance']=self.statement(accepted,role='user')
        self.request['authority']=self.save('bad-authority.json',authority)
        with self.assertRaisesRegex(ContractError,'ASSISTANT'):ext.register_extension(self.save('bad-request.json',self.request))

    def test_unknown_original_attempt_is_retained_and_changed_ledger_invalidates_loader(self):
        # UNKNOWN is retained as evidence, never silently repeated or deleted.
        req=dict(id=1,method='item/tool/call',params=dict(threadId=self.base['thread_id'],turnId=self.previous,
            callId='old-call',namespace='upgrade_collaboration',tool='execute',arguments=dict(operation_id='ready')))
        self.ledger.receive(req);self.ledger.stop('old-call','UNKNOWN')
        ref=self.register();self.assertEqual(ext.load_extension(ref)['base_snapshot']['calls'][0][4],'UNKNOWN')
        self.ledger.db.execute("UPDATE calls SET phase='CANCELLED' WHERE call_id='old-call'")
        with self.assertRaisesRegex(ContractError,'CHANGED'):ext.load_extension(ref)

    def test_pending_original_operation_prevents_auxiliary_handoff(self):
        req=dict(id=1,method='item/tool/call',params=dict(threadId=self.base['thread_id'],turnId=self.previous,
            callId='old-call',namespace='upgrade_collaboration',tool='execute',arguments=dict(operation_id='ready')))
        self.ledger.receive(req)
        with self.assertRaisesRegex(ContractError,'PENDING'):self.register()

    def test_extra_shell_operation_rejected(self):
        self.request['operations']['shell']=dict(action='shell',payload=dict(argv=['touch','arbitrary']))
        self.request_ref=self.accept_request()
        with self.assertRaisesRegex(ContractError,'UNSUPPORTED'):self.register()

    def test_duplicate_base_id_rejected(self):
        self.request['operations']['ready']=self.request['operations']['read'];self.request_ref=self.accept_request()
        with self.assertRaisesRegex(ContractError,'SCOPE'):self.register()

    def test_original_tool_schema_is_unchanged_and_namespaced_by_id(self):
        from upgrade_task_operations import tool_definition
        self.assertEqual(ext.tool_definition(),tool_definition())
        self.assertEqual(ext.invocation_script('one','read'),
            'const result = await tools.upgrade_collaboration__execute({operation_id:"extension:one:read"}); text(result);')


class ServiceTests(CapabilityFixture,unittest.TestCase):
    def test_unknown_service_cannot_reconstruct_and_claim_normal_completion(self):
        service=self.make_service();service.client.respond.side_effect=OSError('lost original response')
        msg,ref,_=self.call(service,'read','one')
        with self.assertRaises(OSError):service.process_recorded(msg,ref)
        unknown=(service.root/'service-unknown.json').read_bytes()
        with self.assertRaisesRegex(ContractError,'NO_RECONSTRUCTION'):
            CapabilityService(service.loader,service.turn,service.activation)
        self.assertEqual((service.root/'service-unknown.json').read_bytes(),unknown)
        self.assertFalse((service.root/'normal-completion.json').exists())
        self.assertEqual(service.client.respond.call_count,1)

    def test_same_activation_cannot_have_second_service_even_before_first_call(self):
        service=self.make_service()
        with self.assertRaisesRegex(ContractError,'NO_RECONSTRUCTION'):
            CapabilityService(service.loader,service.turn,service.activation)
        self.assertFalse(service.closed);service.client.respond.assert_not_called()

    def test_normally_closed_service_cannot_reopen_same_activation(self):
        service=self.make_service()
        message=dict(method='turn/completed',params=dict(threadId=self.base['thread_id'],
            turn=dict(id=self.turn,status='completed')))
        ref=self.save('fixture-end.json',dict(direction='received',peer=self.peer(),message=message))
        service.client.last_received_record=ref;service.process_recorded(message,ref)
        completion=(service.root/'normal-completion.json').read_bytes()
        with self.assertRaisesRegex(ContractError,'NO_RECONSTRUCTION'):
            CapabilityService(service.loader,service.turn,service.activation)
        self.assertEqual((service.root/'normal-completion.json').read_bytes(),completion)

    def test_original_stop_file_prevents_read_dispatch(self):
        service=self.make_service();state=json.loads(Path(self.payload['episode']['path']).read_text())
        stop=self.root/'state'/'executor-reask-v1'/(inquiry._hash(state['caller_surface_uuid'])+'.stop.json')
        stop.write_text('{}')
        msg,ref,_=self.call(service,'read','one')
        with self.assertRaisesRegex(ContractError,'ORIGINAL_EPISODE_STOPPED'):service.process_recorded(msg,ref)
        service.client.respond.assert_not_called()

    def test_non_dynamic_notifications_do_not_close_service(self):
        service=self.make_service()
        messages=[dict(method='serverRequest/resolved',params=dict(requestId='other')),
            dict(method='item/completed',params=dict(threadId=self.base['thread_id'],turnId=self.turn,
                item=dict(type='agentMessage',id='ordinary',text='The original model is working.')))]
        for index,msg in enumerate(messages):
            ref=self.save('notice-'+str(index)+'.json',dict(direction='received',peer=self.peer(),message=msg))
            service.client.last_received_record=ref
            self.assertEqual(service.process_recorded(msg,ref)['phase'],'OBSERVED')
        self.assertFalse(service.closed);service.client.respond.assert_not_called()

    def test_duplicate_call_id_is_refused_before_second_handler_or_response(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one');service.process_recorded(msg,ref)
        self.complete(service,'read','one');msg,ref,_=self.call(service,'answer','one')
        with self.assertRaisesRegex(ContractError,'DUPLICATE_CALL_ID'):service.process_recorded(msg,ref)
        self.assertFalse((service.root/'operations/answer').exists());self.assertEqual(service.client.respond.call_count,1)

    def test_wait_reply_has_synthetic_schema_fields_and_canonical_body_hash(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one');service.process_recorded(msg,ref)
        read,_=self.complete(service,'read','one');statement=self.answer_statement(service,read)
        statement['reply'].update(status='WAITING_DEPENDENCY',owner=statement['reply']['supervisor_uuid'],
            next_due_epoch=180,deadline_epoch=240,overall_task=self.base['task_id'])
        self.statement(statement);msg,ref,_=self.call(service,'answer','two');service.process_recorded(msg,ref)
        result,_=self.complete(service,'answer','two')
        # Synthetic fixture checks the local schema. Actual-controller pure
        # function compatibility is a separately pinned private probe.
        self.assertEqual(result['reply_body_sha256'],digest(json.dumps(statement['reply'],
            ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()))
        self.assertEqual(statement['reply']['owner'],statement['reply']['supervisor_uuid'])

    def test_foreign_overall_task_cannot_borrow_original_reply_template(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one');service.process_recorded(msg,ref)
        read,_=self.complete(service,'read','one');statement=self.answer_statement(service,read)
        statement['reply']['overall_task']='foreign-task';self.statement(statement)
        msg,ref,_=self.call(service,'answer','two')
        with self.assertRaisesRegex(ContractError,'ORIGINAL_OVERALL_TASK'):service.process_recorded(msg,ref)
        self.assertFalse(any((self.root/'state'/'executor-idle-v1'/'mailbox').rglob('*.json')))

    def test_normal_closed_turn_activation_uses_same_client_and_tool_without_base_enrollment(self):
        from upgrade_task_actor import TaskActor,TaskService
        from upgrade_task_enrollment import TaskCampaignGate
        ref=self.register();actor=object.__new__(TaskActor);old=object.__new__(TaskService)
        gate=object.__new__(TaskCampaignGate);client=Mock();client.server=self.server
        actor.client=client;actor.ledger=self.ledger;actor.gate=gate;actor.directory=self.root/'actor'
        actor.directory.mkdir();gate.campaign=dict(runtime=self.runtime,client_isolation=self.adoption)
        old.actor=actor
        def request(method,params):
            self.assertEqual(method,'turn/start');self.assertEqual(params['threadId'],self.base['thread_id'])
            result=dict(turn=dict(id=self.turn))
            client.last_exchange=dict(intent=self.save('start-intent.json',dict(direction='send_intent',
                peer=self.peer(),message=dict(id=45,method=method,params=params))),
                response=self.save('start-response.json',dict(direction='received',peer=self.peer(),
                message=dict(id=45,result=result))))
            return result
        client.request.side_effect=request
        before=ext._ledger_snapshot(self.ledger_pin,self.base)
        with patch.object(TaskService,'_normal_terminal',return_value=self.completion), \
                patch.object(TaskCampaignGate,'_observe',return_value={}), \
                patch.object(ext.ExtensionLoader,'recheck'):
            service=CapabilityService.activate(old,ref)
            self.assertIs(service.client,client)
            self.assertEqual(ext._ledger_snapshot(self.ledger_pin,self.base),before)
            self.assertEqual(client.request.call_count,1)
            self.assertIn('extension:fixture-extension:read',client.request.call_args.args[1]['input'][0]['text'])
            with self.assertRaises((FileExistsError,ContractError)):CapabilityService.activate(old,ref)
            self.assertEqual(client.request.call_count,1)

    def make_service(self):
        ref=self.register();client=Mock();client.server=self.server
        loader=ext.ExtensionLoader(ref,client)
        # Only kernel/process live observations are stubbed. Native records,
        # original ledger reads, request matching, handlers and files are real.
        patcher=patch.object(loader,'recheck',side_effect=lambda turn: self.assertEqual(turn,self.turn))
        patcher.start();self.addCleanup(patcher.stop)
        root=Path(loader.registration['output_root'])
        intent=self.save('fixture-handoff-intent.json',dict(registration=ref))
        exchange=dict(intent=self.save('fixture-start-intent.json',dict(direction='send_intent',peer=self.peer(),
            message=dict(id=98,method='turn/start',params=dict(threadId=self.base['thread_id'],input=[])))),
            response=self.save('fixture-start-response.json',dict(direction='received',peer=self.peer(),
                message=dict(id=98,result=dict(turn=dict(id=self.turn))))))
        activation=self.save(str((root/'activation.json').relative_to(self.root)),
            dict(registration=ref,turn_id=self.turn,handoff_intent=intent,turn_start=exchange))
        return CapabilityService(loader,self.turn,activation)

    def call(self,service,operation,call_id):
        outer=self.append('codex',dict(type='custom_tool_call',name='exec',call_id='outer-'+call_id,
            input=ext.invocation_script(service.loader.registration['extension_id'],operation),
            internal_chat_message_metadata_passthrough=dict(turn_id=self.turn)))
        msg=dict(id=call_id,method='item/tool/call',params=dict(threadId=self.base['thread_id'],turnId=self.turn,
            callId=call_id,namespace='upgrade_collaboration',tool='execute',
            arguments=dict(operation_id=ext.routed_operation(service.loader.registration['extension_id'],operation))))
        ref=self.save('rpc-'+call_id+'.json',dict(direction='received',peer=self.peer(),message=msg))
        service.client.last_received_record=ref
        return msg,ref,outer

    def complete(self,service,operation,call_id):
        result=service.client.respond.call_args.args[0]['result']
        msg=dict(method='item/completed',params=dict(threadId=self.base['thread_id'],turnId=self.turn,
            item=dict(type='dynamicToolCall',id=call_id,namespace='upgrade_collaboration',tool='execute',
                arguments=dict(operation_id=ext.routed_operation(service.loader.registration['extension_id'],operation)),
                status='completed',**result)))
        ref=self.save('completed-'+call_id+'.json',dict(direction='received',peer=self.peer(),message=msg))
        service.client.last_received_record=ref;service.process_recorded(msg,ref)
        value=json.loads(result['contentItems'][0]['text'])
        native=self.append('codex',dict(type='custom_tool_call_output',call_id='outer-'+call_id,
            output=[dict(type='input_text',text='Script completed\nWall time 0.01 seconds\nOutput:\n'),
                    dict(type='input_text',text=json.dumps(value))],
            internal_chat_message_metadata_passthrough=dict(turn_id=self.turn)))
        return value,native

    def answer_statement(self,service,read,**changes):
        state=json.loads(Path(self.payload['episode']['path']).read_text());req=json.loads(read_ref_text(self.payload['request']))
        reply=dict(marker=req['marker'],caller_surface_uuid=state['caller_surface_uuid'],
            supervisor_uuid=state['supervisor'],task_id=self.base['task_id'],episode_id=state['episode_id'],
            status='ACK',trigger='Original inquiry read; next task remains under its original owner.')
        value=dict(schema=inquiry.ANSWER_SCHEMA,task_id=self.base['task_id'],
            extension_id=service.loader.registration['extension_id'],operation_id='answer',
            read_operation_id='read',body_sha256=read['body_sha256'],reply=reply)
        value.update(changes);return value

    def test_full_rpc_read_model_answer_write_response_completion_chain(self):
        service=self.make_service();before=ext._ledger_snapshot(self.ledger_pin,self.base)
        msg,ref,_=self.call(service,'read','one');self.assertEqual(service.process_recorded(msg,ref)['phase'],'RESPONSE_INTENDED')
        read,_=self.complete(service,'read','one');self.assertIn('\nSecond line',read['body'])
        self.assertFalse(read['model_consumption_proven'])
        self.statement(self.answer_statement(service,read))
        msg,ref,_=self.call(service,'answer','two');service.process_recorded(msg,ref)
        result,_=self.complete(service,'answer','two')
        self.assertTrue(result['reply_model_authored']);self.assertFalse(result['executor_consumed'])
        self.assertEqual(json.loads(Path(result['reply']['path']).read_text())['status'],'ACK')
        self.assertEqual(ext._ledger_snapshot(self.ledger_pin,self.base),before)
        self.assertEqual(service.client.respond.call_count,2)

    def test_truncated_read_output_rejects_answer_before_mailbox_write(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one');service.process_recorded(msg,ref)
        read=json.loads(service.client.respond.call_args.args[0]['result']['contentItems'][0]['text'])
        self.statement(self.answer_statement(service,read));msg,ref,_=self.call(service,'answer','two')
        with self.assertRaisesRegex(ContractError,'NATIVE_RESULT'):service.process_recorded(msg,ref)
        self.assertTrue(service.closed);self.assertFalse(any((self.root/'state'/'executor-idle-v1'/'mailbox').rglob('*.json')))

    def test_tool_or_user_echo_is_not_native_model_reply(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one');service.process_recorded(msg,ref)
        read,_=self.complete(service,'read','one');self.statement(self.answer_statement(service,read),role='user')
        msg,ref,_=self.call(service,'answer','two')
        with self.assertRaisesRegex(ContractError,'ONE_NATIVE_STATEMENT'):service.process_recorded(msg,ref)

    def test_mismatched_reply_body_hash_refused(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one');service.process_recorded(msg,ref)
        read,_=self.complete(service,'read','one');self.statement(self.answer_statement(service,read,body_sha256='0'*64))
        msg,ref,_=self.call(service,'answer','two')
        with self.assertRaisesRegex(ContractError,'READ_BINDING'):service.process_recorded(msg,ref)

    def test_lost_response_closes_service_and_cannot_replay(self):
        service=self.make_service();service.client.respond.side_effect=OSError('lost original response')
        msg,ref,_=self.call(service,'read','one')
        with self.assertRaises(OSError):service.process_recorded(msg,ref)
        self.assertTrue(service.closed)
        self.assertTrue((service.root/'operations/read/response-intent.json').exists())
        with self.assertRaisesRegex(ContractError,'CLOSED'):service.process_recorded(msg,ref)
        self.assertEqual(service.client.respond.call_count,1)

    def test_repeating_read_under_new_call_id_is_refused(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one');service.process_recorded(msg,ref)
        self.complete(service,'read','one');msg,ref,_=self.call(service,'read','two')
        with self.assertRaisesRegex(ContractError,'NO_REPLAY'):service.process_recorded(msg,ref)
        self.assertEqual(service.client.respond.call_count,1)

    def test_foreign_rpc_and_arbitrary_arguments_do_not_get_intent(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one')
        msg['params']['arguments']['argv']=['touch','not-allowed'];ref=self.save('bad-rpc.json',
            dict(direction='received',peer=self.peer(),message=msg));service.client.last_received_record=ref
        with self.assertRaisesRegex(ContractError,'ORIGINAL_RPC'):service.process_recorded(msg,ref)
        self.assertFalse((service.root/'operations/read/intent.json').exists())

    def test_direct_handler_without_issued_context_is_refused(self):
        with self.assertRaises(ContractError):inquiry.dispatch('inquiry-read',self.payload,Mock())


def read_ref_text(ref):return Path(ref['path']).read_text()


class ChannelCycleTests(CapabilityFixture,unittest.TestCase):
    make_service=ServiceTests.make_service
    call=ServiceTests.call
    complete=ServiceTests.complete

    def setUp(self):
        super().setUp()
        state=json.loads(Path(self.payload['episode']['path']).read_text())
        state.update(marker_secret='1'*32,attempt_count=1,replies_closed_through=0,next_due_epoch=180.)
        req=json.loads(read_ref_text(self.payload['request']));marker=self.marker(state,1)
        state['asks'][0]['marker']=marker;req['marker']=marker
        req['reply_mailbox']=str(Path(state['mailbox'])/(marker+'.json'))
        Path(self.payload['episode']['path']).write_bytes(canonical(state))
        channel=self.payload.pop('request')['path'];Path(channel).write_bytes(canonical(req))
        self.payload.update(schema=inquiry.CHANNEL_SCHEMA,channel=channel,slot=1)
        second=dict(self.payload,slot=2,previous_read_operation_id='read')
        self.request['operations']={
            'read':dict(action='inquiry-read',payload=copy.deepcopy(self.payload)),
            'answer':dict(action='inquiry-answer',payload=dict(self.payload,read_operation_id='read')),
            'read2':dict(action='inquiry-read',payload=second),
            'answer2':dict(action='inquiry-answer',payload=dict(second,read_operation_id='read2'))}
        self.request_ref=self.accept_request()

    def marker(self,state,ordinal):
        signature=hmac.new(state['marker_secret'].encode(),str(ordinal).encode(),hashlib.sha256).hexdigest()[:16]
        return f'EXECUTOR_READY_{ordinal:x}_{signature}'

    def answer_statement(self,service,read,*,operation='answer',read_id='read',**changes):
        state=json.loads(Path(self.payload['episode']['path']).read_text())
        reply=dict(marker=read['marker'],caller_surface_uuid=state['caller_surface_uuid'],
            supervisor_uuid=state['supervisor'],task_id=self.base['task_id'],episode_id=state['episode_id'],
            status='ACK',trigger='Complete original request read; original controller retains scheduling.')
        value=dict(schema=inquiry.ANSWER_SCHEMA,task_id=self.base['task_id'],
            extension_id=service.loader.registration['extension_id'],operation_id=operation,
            read_operation_id=read_id,body_sha256=read['body_sha256'],reply=reply)
        value.update(changes);return value

    def next_ask(self,now=190.):
        path=Path(self.payload['episode']['path']);state=json.loads(path.read_text())
        attempt=state['attempt_count']+1;marker=self.marker(state,attempt)
        body='The next complete inquiry body.'
        ask=dict(marker=marker,attempt=attempt,at_epoch=now,due_epoch=180.,text_sha256=inquiry._hash(body),
            channels=dict(written=[self.payload['channel']],errors=[]),outcome='FILE_ONLY_SCHEDULED',
            terminal=False,input_operations=0)
        state['asks'].append(ask);state['attempt_count']=attempt;state['state']='WAITING_REPLY'
        path.write_bytes(canonical(state))
        req=json.loads(Path(self.payload['channel']).read_text());req.update(marker=marker,at_epoch=now,
            request=body,reply_mailbox=str(Path(state['mailbox'])/(marker+'.json')))
        Path(self.payload['channel']).write_bytes(canonical(req));return state

    def test_registration_is_not_invalidated_by_current_channel_replacement(self):
        ref=self.register();self.next_ask();self.assertEqual(ext.load_extension(ref)['request'],self.request_ref)

    def test_answer_uses_original_snapshot_after_new_channel_ask(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one');service.process_recorded(msg,ref)
        read,_=self.complete(service,'read','one');state=self.next_ask()
        self.statement(self.answer_statement(service,read));msg,ref,_=self.call(service,'answer','two')
        service.process_recorded(msg,ref);result,_=self.complete(service,'answer','two')
        self.assertEqual(json.loads(read_ref_text(result['reply']))['marker'],read['marker'])
        self.assertNotEqual(read['marker'],state['asks'][-1]['marker'])

    def test_second_cycle_requires_original_consumption(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one');service.process_recorded(msg,ref)
        read,_=self.complete(service,'read','one');self.statement(self.answer_statement(service,read))
        msg,ref,_=self.call(service,'answer','two');service.process_recorded(msg,ref);self.complete(service,'answer','two')
        self.next_ask();msg,ref,_=self.call(service,'read2','three')
        with self.assertRaises((ContractError,FileNotFoundError)):service.process_recorded(msg,ref)
        self.assertEqual(service.client.respond.call_count,2)

    def test_another_extension_cannot_reset_original_slot_or_marker(self):
        service=self.make_service();msg,ref,_=self.call(service,'read','one');service.process_recorded(msg,ref)
        self.complete(service,'read','one');self.request['extension_id']='another-extension'
        self.request_ref=self.accept_request();second=self.make_service()
        msg,ref,_=self.call(second,'read','other')
        with self.assertRaisesRegex(ContractError,'CYCLE_OR_MARKER_ALREADY'):second.process_recorded(msg,ref)
        second.client.respond.assert_not_called()

    def test_channel_must_be_derived_original_path(self):
        for op in self.request['operations'].values():op['payload']['channel']=str(self.root/'foreign.json')
        self.request_ref=self.accept_request()
        with self.assertRaisesRegex(ContractError,'ORIGINAL_PATH'):self.register()

    def test_third_slot_is_not_a_new_budget(self):
        self.request['operations']['read2']['payload']['slot']=3
        self.request_ref=self.accept_request()
        with self.assertRaises(ContractError):self.register()


if __name__=='__main__':unittest.main()
