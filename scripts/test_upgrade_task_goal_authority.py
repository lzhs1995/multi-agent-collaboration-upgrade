"""Isolated native-journal tests. No user paths or live mutation in fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from orchestration_common import ContractError, canonical, digest, file_ref, write_json
from test_orchestration_native import NativeFixture
import upgrade_task_goal_authority as goal


class GoalAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.f = NativeFixture(self.root, 'codex')
        self.sid = self.f.session['session_id']
        self.turn = '01a15a50-c0e5-7de1-9e3e-48b5b7871ca8'
        self.body = b'Continue this task.\nKeep the original limits.\n'
        self.path = self.root/'attachment.txt';self.path.write_bytes(self.body)
        self.context = dict(original_session_id=self.sid, goal_created_at=1792516554,
            attachment_bytes=len(self.body), attachment_sha256=digest(self.body))
        self.command = 'rtk proxy sed -n \'1,260p\' ' + str(self.path)
        self.initial = dict(timestamp='2026-10-20T17:15:54.886Z', type='event_msg', payload=dict(
            type='thread_goal_updated', threadId=self.sid, goal=dict(threadId=self.sid,
            objective='pasted text file: '+str(self.path)+'. Read this file before continuing.',
            status='active', tokensUsed=0, timeUsedSeconds=0, createdAt=1792516554, updatedAt=1792516554)))
        self.call = dict(timestamp='2026-10-20T17:17:00.029Z', type='response_item', payload=dict(
            type='custom_tool_call', name='exec', call_id='call-original-read',
            input='text(await tools.exec_command({cmd:'+json.dumps(self.command)+'}));',
            internal_chat_message_metadata_passthrough=dict(turn_id=self.turn)))
        self.execution = dict(timestamp='2026-10-20T17:17:01.398Z', type='event_msg', payload=dict(
            type='item_completed', thread_id=self.sid, turn_id=self.turn, item=dict(type='CommandExecution',
            command=['/bin/zsh','-c',self.command],status='completed',exit_code=0,
            stdout=self.body.decode(),stderr='',aggregated_output=self.body.decode())))
        self.output = dict(timestamp='2026-10-20T17:17:03.949Z', type='response_item', payload=dict(
            type='custom_tool_call_output',call_id='call-original-read',
            internal_chat_message_metadata_passthrough=dict(turn_id=self.turn),output=[
            dict(type='input_text',text='Script completed\n'),dict(type='input_text',
                text=json.dumps(dict(exit_code=0,output=self.body.decode())))]))

    def tearDown(self):
        self.tmp.cleanup()

    def pack(self, *, gap=None):
        # Each call creates one fresh logical chain in this isolated journal.
        refs={'goal_record':self.f.append(self.initial)}
        if gap:self.f.append(gap)
        refs.update(read_call=self.f.append(self.call),read_execution=self.f.append(self.execution),
                    read_output=self.f.append(self.output))
        return dict(schema=goal.SCHEMA,session=self.f.session,goal_created_at=1792516554,
                    attachment=file_ref(self.path),**refs)

    def verify(self, value=None, context=None):
        return goal._verify(value or self.pack(),None,context or self.context,fixtures=True)

    def test_exact_goal_read_and_decoded_native_output_pass_without_user_claim(self):
        proof=self.verify()
        self.assertEqual(proof['kind'],'native_goal_attachment_read')
        self.assertFalse(proof['native_user_message'])
        self.assertEqual(proof['attachment_text_sha256'],digest(self.body))

    def test_public_entry_has_no_fixture_bypass_and_rejects_copied_journal(self):
        ref=write_json(self.root/'evidence.json',self.pack())
        with self.assertRaises(ContractError):
            goal.verify_goal_authority(ref,expected_context=self.context)

    def test_generated_goal_prompt_cannot_substitute_goal_setting(self):
        self.initial=dict(timestamp=self.initial['timestamp'],type='response_item',payload=dict(
            type='message',role='user',content=[dict(type='input_text',text='<codex_internal_context source="goal">Continue</codex_internal_context>')]))
        with self.assertRaisesRegex(ContractError,'INITIAL_GOAL_REQUIRED'):self.verify()

    def test_caller_context_pins_reject_unrelated_goal_attachment(self):
        for field,value in [('attachment_sha256','0'*64),('attachment_bytes',len(self.body)+1),
                            ('goal_created_at',1792516555),('original_session_id','another-session')]:
            with self.subTest(field=field):
                context=dict(self.context);context[field]=value
                with self.assertRaises(ContractError):self.verify(context=context)

    def test_native_output_full_bytes_required_not_current_file_alone(self):
        self.execution['payload']['item']['stdout']=self.body.decode()[:-1]
        self.execution['payload']['item']['aggregated_output']=self.body.decode()[:-1]
        with self.assertRaisesRegex(ContractError,'COMPLETE_NATIVE_READ_REQUIRED'):self.verify()

    def test_truncated_or_failed_model_return_rejected(self):
        for answer in [dict(exit_code=0,output=self.body.decode()[:-1]),
                       dict(exit_code=1,output=self.body.decode()),
                       dict(exit_code=0,output=self.body.decode(),session_id=123)]:
            with self.subTest(answer=answer):
                self.output['payload']['output'][1]['text']=json.dumps(answer)
                with self.assertRaisesRegex(ContractError,'FULL_CONTENT_NOT_RETURNED'):self.verify()

    def test_foreign_call_and_turn_cannot_join_read_chain(self):
        original=copy.deepcopy(self.output)
        for field in ['call_id','turn_id']:
            self.output=copy.deepcopy(original)
            if field=='call_id':self.output['payload']['call_id']='foreign'
            else:self.output['payload']['internal_chat_message_metadata_passthrough']['turn_id']='foreign'
            with self.subTest(field=field):
                with self.assertRaisesRegex(ContractError,'SAME_CALL_OUTPUT_REQUIRED'):self.verify()

    def test_failed_execution_or_foreign_native_session_rejected(self):
        self.execution['payload']['item']['exit_code']=1
        with self.assertRaisesRegex(ContractError,'COMPLETE_NATIVE_READ_REQUIRED'):self.verify()
        self.execution['payload']['item']['exit_code']=0
        self.execution['payload']['thread_id']='foreign'
        with self.assertRaisesRegex(ContractError,'COMPLETE_NATIVE_READ_REQUIRED'):self.verify()

    def test_partial_sed_and_shell_mutation_are_not_literal_full_reads(self):
        for cmd in ['rtk proxy sed -n \'1,1p\' '+str(self.path),
                    'rtk proxy cat '+str(self.path)+'; true']:
            self.execution['payload']['item']['command'][-1]=cmd
            self.call['payload']['input']='text(await tools.exec_command({cmd:'+json.dumps(cmd)+'}));'
            with self.subTest(cmd=cmd):
                with self.assertRaises(ContractError):self.verify()

    def test_repinned_changed_attachment_still_fails_current_authorized_context(self):
        self.path.write_bytes(self.body+b'Another instruction.\n')
        with self.assertRaisesRegex(ContractError,'ATTACHMENT_CONTEXT_CHANGED'):self.verify()

    def test_native_record_hash_and_order_are_checked(self):
        value=self.pack();value['read_execution']['sha256']='0'*64
        with self.assertRaisesRegex(ContractError,'hash/length'):self.verify(value)
        value=self.pack();value['read_call'],value['read_output']=value['read_output'],value['read_call']
        with self.assertRaisesRegex(ContractError,'RECORD_ORDER_INVALID'):self.verify(value)

    def test_goal_replacement_or_pause_before_read_rejected(self):
        for field,val in [('objective','Different goal'),('status','paused')]:
            gap=copy.deepcopy(self.initial);gap['payload']['goal'][field]=val
            with self.subTest(field=field):
                with self.assertRaisesRegex(ContractError,'GOAL_CHANGED_BEFORE_READ'):
                    self.verify(self.pack(gap=gap))

    def test_same_goal_progress_update_is_not_new_authorization(self):
        gap=copy.deepcopy(self.initial);gap['payload']['goal'].update(updatedAt=1792516604,timeUsedSeconds=45)
        self.assertFalse(self.verify(self.pack(gap=gap))['native_user_message'])

    def test_late_read_and_duplicate_output_rejected(self):
        self.output['timestamp']='2026-10-20T18:17:03.949Z'
        with self.assertRaisesRegex(ContractError,'READ_TIME_INVALID'):self.verify()
        self.output['timestamp']='2026-10-20T17:17:03.949Z'
        self.output['payload']['output'].append(copy.deepcopy(self.output['payload']['output'][1]))
        with self.assertRaisesRegex(ContractError,'FULL_CONTENT_NOT_RETURNED'):self.verify()


if __name__=='__main__':unittest.main()
