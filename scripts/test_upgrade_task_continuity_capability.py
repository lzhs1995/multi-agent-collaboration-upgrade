"""Isolated controller fixtures: no live authority, application or model calls."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import copy
import sys
import tempfile
import unittest

import upgrade_task_continuity_capability as c
from orchestration_common import ContractError, file_ref, json_ref, require, tree_manifest, write_json


class Reader:
    def snapshot(self, **kwargs): return {'offset': 100}
    def verify_pin(self, pin): return pin['offset']


class FixtureContext:
    def __init__(self, case, operation):
        self.case = case
        self.registration = case.reg
        self.registration_ref = case.reg_ref
        self.operation_id = operation
        self.task_id = 'original-task'
        self.sessions = {lane: {'session_id':lane + '-original', 'surface_uuid':lane + '-surface'}
                         for lane in c.LANES}
        self.directory = Path(case.reg['output_root'])/'operations'/operation
        self.directory.mkdir(parents=True, exist_ok=True)
        self.statements = {}
        self.calls = []
    def assert_operation(self, action, payload):
        require(self.registration['operations'][self.operation_id] == dict(action=action,payload=payload),'fixture scope')
    def recheck(self): pass
    def reader(self, lane): return Reader()
    def native_statement(self, ref, schema, lane='codex', *, after=0):
        self.calls.append((schema,lane,after))
        return self.statements[(schema,lane)]
    def completed_operation(self, operation):
        require(operation == 'work','fixture work id')
        return self.case.completed_ref, self.case.completed, {'offset':101,'length':20}


class ContinuityCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.authority = self.put('authority.json', {'original':'user authorization'})
        self.base = self.put('base.json', {'task_id':'original-task','authority_ref':self.authority})
        self.budget = self.put('budget.json', dict(schema=c.BUDGET,task_id='original-task',max_subagents=2,
            child_budget_scope='WHOLE_TASK',children_used=['child-original-1','child-original-2'],
            review_limit=3,review_rounds_used=1,review_history=[self.put('old-review.json',{'failed':True})]))
        self.scope = dict(scope_id='chapter',mode='CODEX_SOLO',writer_lane='codex',output_name='chapter.md',
            inputs=[self.put('input.json',{'original':True})],handoff=self.put('handoff.json',{'open':'existing gap'}),
            nonce_state=self.put('nonce.json',dict(schema='upgrade-task-continuity-guard-v1',task_id='original-task',
                scope_id='chapter',kind='nonce_state',state='CLEAR',pending=[])),
            lease_state=self.put('lease.json',dict(schema='upgrade-task-continuity-guard-v1',task_id='original-task',
                scope_id='chapter',kind='lease_state',state='NONE',pending=[])))
        self.setup_contract()
        self.reg = dict(task_id='original-task',extension_id='extension-one',base_registration=self.base,
            authority=self.authority,budget=self.budget,runtime={'fixture':True},adoption={'fixture':True},
            output_root=str(self.root/'task-capability-extensions/extension-one'),operations={})
        self.set_operations();self.reg_ref=self.put('extension.json',self.reg)
        self.allowed = patch('upgrade_task_capability_extension.require_context',lambda context:context)
        self.allowed.start();self.addCleanup(self.allowed.stop)
        self.evaluation = patch.object(c,'_evaluate',lambda context,contract:dict(collection={'fixture_only':True},
            original={'plan':dict(mode=self.scope['mode'])}))
        self.evaluation.start();self.addCleanup(self.evaluation.stop)
    def put(self,name,value):
        return write_json(self.root/name,value,exclusive=True)
    def setup_contract(self):
        suffix = str(len(list(self.root.glob('checkpoint*.json'))))
        checkpoint=self.put('checkpoint'+suffix+'.json',dict(schema=c.CHECKPOINT,task_id='original-task',
            base_registration=self.base,budget=self.budget,scopes=[self.scope]))
        self.contract=self.put('contract'+suffix+'.json',dict(schema=c.CONTRACT,task_id='original-task',
            base_registration=self.base,original_authority=self.authority,continuity_registration={'fixture':True},
            budget=self.budget,checkpoint=checkpoint))
    def set_operations(self):
        self.reg['operations']={'work':dict(action='continuity-work',payload=dict(contract=self.contract,scope_id='chapter')),
            'result':dict(action='continuity-result',payload=dict(contract=self.contract,scope_id='chapter',
                work_operation_id='work',statement=None,review_statement=None))}
    def call(self,operation,context=None):
        op=self.reg['operations'][operation];context=context or FixtureContext(self,operation)
        return c.dispatch(op['action'],op['payload'],context)
    def work(self):
        self.completed=self.call('work');self.completed_ref=self.put('completed.json',self.completed)
        return self.completed
    def result_context(self):
        ctx=FixtureContext(self,'result')
        ctx.statements[(c.SUBMISSION,self.scope['writer_lane'])]=dict(schema=c.SUBMISSION,task_id=ctx.task_id,
            extension_id=self.reg['extension_id'],operation_id='result',work=self.completed['work'],
            text='Original scoped content.\n',self_review='Checked original limitation.',limitations=['Historical gap stays open.'])
        return ctx
    def test_solo_publishes_fixed_artifact_and_retains_spent_budgets(self):
        self.work();ctx=self.result_context();result=self.call('result',ctx)['value']
        self.assertEqual(result['validation'],'solo_self_review')
        self.assertEqual(Path(result['artifact']['path']).read_text(),'Original scoped content.\n')
        self.assertEqual(result['children_used'],['child-original-1','child-original-2'])
        self.assertEqual(result['review_round'],2)
        self.assertFalse(result['formal_business_accepted']);self.assertEqual(result['resource_releases'],0)
        self.assertEqual(ctx.calls,[(c.SUBMISSION,'codex',100)])
    def test_work_returns_complete_original_inputs_and_handoff(self):
        work=self.work()['value']
        self.assertEqual(len(work['input_bodies']),2)
        for body in work['input_bodies']:
            self.assertEqual(body['full_text'],Path(body['reference']['path']).read_text())
        self.assertEqual([x['kind'] for x in work['input_bodies']],['input','handoff'])
        ctx=self.result_context();self.assertEqual(self.call('result',ctx)['value']['review_round'],2)
    def test_nontext_input_never_claims_a_complete_model_read(self):
        path=self.root/'binary.dat';path.write_bytes(b'\xff')
        self.scope['inputs']=[file_ref(path)];self.setup_contract();self.set_operations()
        with self.assertRaisesRegex(ValueError,'UTF8_TEXT'):self.call('work')

    def test_duplicate_scope_even_new_extension_cannot_claim_again(self):
        self.work();self.reg['extension_id']='extension-two';self.reg['output_root']=str(self.root/'task-capability-extensions/extension-two')
        with self.assertRaisesRegex(ContractError,'ALREADY_CLAIMED'):self.call('work')
    def test_original_contract_and_child_budget_cannot_reset(self):
        self.work();self.setup_contract();self.set_operations()
        with self.assertRaisesRegex(ContractError,'NO_CONTRACT_OR_BUDGET_RESET'):self.call('work')
    def test_result_without_full_native_work_output_is_rejected(self):
        self.work();self.completed=dict(self.completed,forged=True)
        with self.assertRaisesRegex(ContractError,'WORK_NOT_CONSUMED'):self.call('result',self.result_context())
    def test_unknown_nonce_never_releases_or_creates_scope(self):
        guard=json_ref(self.scope['nonce_state']);guard['state']='UNKNOWN'
        self.scope['nonce_state']=self.put('unknown-nonce.json',guard);self.setup_contract();self.set_operations()
        with self.assertRaisesRegex(ContractError,'UNKNOWN_NO_RELEASE'):self.call('work')
        self.assertFalse((self.root/'task-capability-extensions/continuity-ledger').exists())
    def test_active_lease_never_releases(self):
        guard=json_ref(self.scope['lease_state']);guard['state']='ACTIVE'
        self.scope['lease_state']=self.put('active-lease.json',guard);self.setup_contract();self.set_operations()
        with self.assertRaisesRegex(ContractError,'UNKNOWN_NO_RELEASE'):self.call('work')
    def test_guard_change_between_work_and_result_refuses_publication(self):
        self.work();Path(self.scope['nonce_state']['path']).write_text('{}')
        with self.assertRaises(ContractError):self.call('result',self.result_context())
    def test_commit_failure_after_artifact_is_unknown_not_replayed(self):
        self.work();ctx=self.result_context();real=c.write_json
        def fail(path,value,**kw):
            if Path(path).name=='result.json':raise OSError('simulated commit loss')
            return real(path,value,**kw)
        with patch.object(c,'write_json',side_effect=fail):
            with self.assertRaises(OSError):self.call('result',ctx)
        with self.assertRaisesRegex(ContractError,'UNKNOWN_RESULT'):self.call('result',ctx)
        shared,directory=c._paths(self.reg,'chapter')
        self.assertTrue((directory/'chapter.md').exists());self.assertTrue((directory/'result-intent.json').exists())
    def test_missing_budget_commit_after_receipt_remains_unknown(self):
        self.work();ctx=self.result_context();real=c.write_json
        def fail(path,value,**kw):
            if Path(path).name.startswith('review-'):raise OSError('simulated budget commit loss')
            return real(path,value,**kw)
        with patch.object(c,'write_json',side_effect=fail):
            with self.assertRaises(OSError):self.call('result',ctx)
        with self.assertRaisesRegex(ContractError,'REVIEW_COMMIT_UNKNOWN'):self.call('result',ctx)
    def test_original_review_limit_exhausted_is_not_renewed(self):
        b=json_ref(self.budget);b['review_rounds_used']=3
        b['review_history'] += [self.put('old2.json',{'round':2}),self.put('old3.json',{'round':3})]
        self.budget=self.put('budget-spent.json',b);self.reg['budget']=self.budget;self.setup_contract();self.set_operations()
        with self.assertRaisesRegex(ContractError,'BUDGET_EXHAUSTED'):self.call('work')
    def test_claude_peer_requires_other_original_lane_and_exact_hash(self):
        self.scope.update(mode='CLAUDE_PEER',writer_lane='claude_a');self.setup_contract();self.set_operations();self.work()
        ctx=self.result_context();sub=ctx.statements[(c.SUBMISSION,'claude_a')]
        ctx.statements[(c.REVIEW,'claude_b')]=dict(schema=c.REVIEW,task_id=ctx.task_id,extension_id=self.reg['extension_id'],
            operation_id='result',work=self.completed['work'],artifact_sha256=c.digest(sub['text'].encode()),verdict='accept',reason='Reviewed original content and limitations.')
        result=self.call('result',ctx)['value'];self.assertEqual(result['validation'],'claude_peer_review')
        self.assertEqual(result['peer_session'],'claude_b-original');self.assertFalse(result['live_transport_verified'])
    def test_claude_peer_wrong_hash_never_publishes(self):
        self.scope.update(mode='CLAUDE_PEER',writer_lane='claude_a');self.setup_contract();self.set_operations();self.work()
        ctx=self.result_context();ctx.statements[(c.REVIEW,'claude_b')]=dict(schema=c.REVIEW,task_id=ctx.task_id,
            extension_id=self.reg['extension_id'],operation_id='result',work=self.completed['work'],artifact_sha256='0'*64,verdict='accept',reason='Wrong bytes')
        with self.assertRaisesRegex(ContractError,'INDEPENDENT_CLAUDE'):self.call('result',ctx)
    def test_rejected_peer_review_is_recorded_and_spends_the_original_round(self):
        self.scope.update(mode='CLAUDE_PEER',writer_lane='claude_a');self.setup_contract();self.set_operations();self.work()
        ctx=self.result_context();sub=ctx.statements[(c.SUBMISSION,'claude_a')]
        ctx.statements[(c.REVIEW,'claude_b')]=dict(schema=c.REVIEW,task_id=ctx.task_id,extension_id=self.reg['extension_id'],
            operation_id='result',work=self.completed['work'],artifact_sha256=c.digest(sub['text'].encode()),verdict='reject',reason='Original limitation unresolved.')
        result=self.call('result',ctx)['value']
        self.assertEqual(result['review_round'],2);self.assertEqual(result['review_verdict'],'reject')
        self.assertFalse(result['peer_accepted'])
        shared,_=c._paths(self.reg,'chapter');self.assertTrue((shared/'review-002.json').exists())
        with self.assertRaisesRegex(ContractError,'ALREADY_ATTEMPTED'):self.call('result',ctx)

    def test_original_native_mode_cannot_be_replaced_by_contract_label(self):
        with patch.object(c,'_evaluate',return_value={'original':{'plan':{'mode':'CLAUDE_PEER'}}}):
            with self.assertRaisesRegex(ContractError,'MODE_CHANGED'):self.call('work')
    def test_solo_cannot_attach_a_peer_claim(self):
        self.work();self.reg['operations']['result']['payload']['review_statement']={'fake':True}
        with self.assertRaisesRegex(ContractError,'SOLO_CANNOT'):self.call('result',self.result_context())
    def test_generic_argv_and_path_escape_are_rejected(self):
        self.reg['operations']['work']['payload']['argv']=['sh','-c','true']
        with self.assertRaisesRegex(ContractError,'OPERATION_FIELDS'):self.call('work')
        del self.reg['operations']['work']['payload']['argv']
        self.scope['output_name']='../original.md';self.setup_contract();self.set_operations()
        with self.assertRaisesRegex(ContractError,'FIXED_ARTIFACT'):self.call('work')
    def test_forged_dictionary_or_object_has_no_live_context_authority(self):
        self.allowed.stop()
        with self.assertRaisesRegex(ContractError,'LIVE_CONTEXT'):c.dispatch('continuity-work',{},SimpleNamespace())
        self.allowed.start()

    def mixed_scope(self):
        self.scope.update(mode='CODEX_CLAUDE', writer_lane='codex')
        self.setup_contract(); self.set_operations()

    def mixed_plan(self, unavailable='claude_b'):
        health = {lane + '-surface': {'state': 'UNAVAILABLE' if lane == unavailable else 'AVAILABLE'}
                  for lane in c.LANES}
        return dict(mode='CODEX_CLAUDE', health=health,
                    active=['codex-surface', ('claude_a' if unavailable == 'claude_b' else 'claude_b') + '-surface'])

    def mixed_work(self, plan=None):
        self.mixed_scope()
        with patch.object(c, '_evaluate', return_value={'original': {'plan': plan or self.mixed_plan()}}):
            return self.work()

    def test_codex_claude_either_original_peer_can_remain_available(self):
        for unavailable in ('claude_a', 'claude_b'):
            # Each lane case has its own original task files and one result attempt.
            case = ContinuityCapabilityTests('runTest'); case.setUp()
            try:
                case.mixed_work(case.mixed_plan(unavailable))
                ctx = case.result_context()
                result = case.call('result', ctx)['value']
                self.assertEqual(Path(result['artifact']['path']).read_text(), 'Original scoped content.\n')
                self.assertEqual(result['writer_session'], 'codex-original')
                self.assertEqual(result['validation'], 'solo_self_review')
                self.assertEqual(result['review_round'], 2)
                self.assertEqual(result['children_used'], ['child-original-1', 'child-original-2'])
                self.assertIsNone(result['peer_session']); self.assertFalse(result['peer_accepted'])
                self.assertFalse(result['formal_business_accepted']); self.assertFalse(result['live_transport_verified'])
                self.assertEqual(result['input_operations'], 0); self.assertEqual(result['resource_releases'], 0)
                self.assertEqual(case.completed['value']['scope']['mode'], 'CODEX_CLAUDE')
                self.assertEqual(ctx.calls, [(c.SUBMISSION, 'codex', 100)])
            finally:
                case.doCleanups()

    def test_codex_claude_rejects_unknown_health_active_set_and_foreign_surface(self):
        self.mixed_scope()
        invalid = []
        for state in ('UNKNOWN', 'PENDING', 'AVAILABLE'):
            plan = self.mixed_plan(); plan['health']['claude_b-surface']['state'] = state
            invalid.append(plan)
        plan = self.mixed_plan(); plan['health']['codex-surface']['state'] = 'UNAVAILABLE'; invalid.append(plan)
        for active in (['codex-surface', 'claude_b-surface'],
                       ['codex-surface', 'codex-surface'], ['codex-surface', 'foreign-surface']):
            plan = self.mixed_plan(); plan['active'] = active; invalid.append(plan)
        plan = self.mixed_plan()
        plan['health']['foreign-surface'] = plan['health'].pop('claude_b-surface'); invalid.append(plan)
        for plan in invalid:
            with self.subTest(plan=plan), patch.object(c, '_evaluate', return_value={'original': {'plan': plan}}):
                with self.assertRaisesRegex(ContractError, 'CONTINUITY_CODEX_CLAUDE'):
                    self.call('work')
                _, directory = c._paths(self.reg, 'chapter')
                self.assertFalse(directory.exists())

    def test_codex_claude_does_not_relabel_another_original_mode(self):
        self.mixed_scope()
        for mode in ('TRIO', 'CODEX_SOLO'):
            plan = self.mixed_plan(); plan['mode'] = mode
            with self.subTest(mode=mode), patch.object(c, '_evaluate', return_value={'original': {'plan': plan}}):
                with self.assertRaisesRegex(ContractError, 'MODE_CHANGED'):
                    self.call('work')
        _, directory = c._paths(self.reg, 'chapter')
        self.assertFalse(directory.exists())

    def test_codex_claude_cannot_assign_writer_or_review_to_the_healthy_peer(self):
        self.mixed_scope(); self.scope['writer_lane'] = 'claude_a'
        self.setup_contract(); self.set_operations()
        with self.assertRaisesRegex(ContractError, 'SINGLE_WRITER'):
            self.call('work')
        self.mixed_work()
        self.reg['operations']['result']['payload']['review_statement'] = {'fake': True}
        with self.assertRaisesRegex(ContractError, 'SOLO_CANNOT'):
            self.call('result', self.result_context())
        _, directory = c._paths(self.reg, 'chapter')
        self.assertFalse((directory/'result-intent.json').exists())

    def test_codex_claude_retains_native_body_and_unknown_result_guards(self):
        self.mixed_work(); ctx = self.result_context()
        original = self.completed; self.completed = dict(original, forged=True)
        with self.assertRaisesRegex(ContractError, 'WORK_NOT_CONSUMED'):
            self.call('result', ctx)
        self.completed = original
        real = c.write_json
        def fail(path, value, **kwargs):
            if Path(path).name == 'result.json': raise OSError('simulated commit loss')
            return real(path, value, **kwargs)
        with patch.object(c, 'write_json', side_effect=fail):
            with self.assertRaises(OSError): self.call('result', ctx)
        with self.assertRaisesRegex(ContractError, 'UNKNOWN_RESULT'):
            self.call('result', ctx)
        _, directory = c._paths(self.reg, 'chapter')
        self.assertTrue((directory/'chapter.md').exists())
        self.assertTrue((directory/'result-intent.json').exists())

    def test_codex_claude_mode_comes_from_original_native_evaluator(self):
        import upgrade_continuity_native as native
        from test_upgrade_continuity_native import NativeContinuityTests, MODERN_SOURCE
        if not MODERN_SOURCE.is_dir():
            self.skipTest('requires complete fixed original MAC0.4.58')
        journals = NativeContinuityTests('runTest'); journals.setUp()
        self.addCleanup(journals.doCleanups)
        journals.codex_success(10); journals.claude_success('claude_a', 10)
        for epoch in (30, 90, 150): journals.claude_error('claude_b', epoch, 502)
        self.mixed_scope()
        ctx = FixtureContext(self, 'work'); ctx.sessions = journals.sessions
        registered = dict(task_id=ctx.task_id, runtime=ctx.registration['runtime'],
                          adoption=ctx.registration['adoption'], sessions=journals.sessions,
                          authorization=self.authority)
        runtime = dict(controller=tree_manifest(MODERN_SOURCE.resolve()),
                       python=file_ref(Path(sys.executable).resolve()))
        collect, invoke = native.collect_native_outcomes, native.invoke_original_plan
        def fixture_collect(sessions, **kwargs):
            kwargs['fixtures'] = True
            return collect(sessions, **kwargs)
        def fixture_invoke(*args, **kwargs):
            kwargs.pop('runtime_ref'); kwargs.pop('adoption_ref')
            return invoke(*args, fixtures=True, **kwargs)
        # Only deployment/runtime boundaries are fixtures. Parse real-format
        # journals and run the complete pinned original evaluator, not a mode stub.
        self.evaluation.stop()
        with patch.object(native, 'validate_native_registration', return_value=(registered, runtime)), \
             patch.object(native, 'collect_native_outcomes', side_effect=fixture_collect), \
             patch.object(native, 'invoke_original_plan', side_effect=fixture_invoke) as evaluator:
            # Retryable failures lack authoritative Retry-After and remain blocked.
            with patch.object(c.time, 'time', return_value=160):
                with self.assertRaisesRegex(ContractError, 'NATIVE_PENDING_NO_TAKEOVER'):
                    self.call('work', ctx)
            evaluator.assert_not_called()
            _, directory = c._paths(self.reg, 'chapter')
            self.assertFalse(directory.exists())
            journals.claude_error('claude_b', 210, 403)
            with patch.object(c.time, 'time', return_value=220):
                work = self.call('work', ctx)['value']
            self.assertEqual(evaluator.call_count, 1)
        plan = work['native_plan']['original']['plan']
        self.assertEqual(plan['mode'], 'CODEX_CLAUDE')
        self.assertEqual(plan['active'], [ctx.sessions[lane]['surface_uuid'] for lane in ('codex', 'claude_a')])
        self.assertEqual(plan['health'][ctx.sessions['claude_b']['surface_uuid']]['state'], 'UNAVAILABLE')
        self.assertEqual(work['scope']['writer_lane'], 'codex')
        self.assertEqual(work['input_operations'], 0); self.assertEqual(work['resource_releases'], 0)


if __name__=='__main__':unittest.main()
