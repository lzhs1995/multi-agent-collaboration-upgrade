"""Generated slots preserve one ledger and one attempt under contention."""
import copy
import multiprocessing
import unittest

from orchestration_common import ContractError, canonical, file_ref, object_sha, write_json
from upgrade_task_bindings import BINDING_SCHEMA, SLOT_SCHEMA
from upgrade_task_operations import OperationLedger
import test_upgrade_task_turns as fixtures


def contender(path, sha, binding, output):
    ledger=OperationLedger(path,sha)
    try:
        ledger.bind_artifact("accept",binding)
        output.put("bound")
    except ContractError:
        output.put("refused")
    finally:
        ledger.close()


class ArtifactBindingTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.TaskTurnTests();f.setUp();self.addCleanup(f.doCleanups);self.f=f
        self.root=f.f.root
        slot=dict(schema=SLOT_SCHEMA,resolver="campaign-accept-marker-v1",
                  source_path=str(self.root/"candidate.json"),producer_operation="probe")
        ops=copy.deepcopy(f.registration["operations"])
        ops.update(accept=dict(kind="fixture",payload={"marker":None}),
                   reject=dict(kind="fixture",payload={"marker":"reject"}))
        self.registration=dict(f.registration,schema="upgrade-task-operations-v3",operations=ops,
            binding_slots={"accept":slot},initial_operations=list(ops),choice_groups={"decision":["accept","reject"]})
        self.ledger=OperationLedger.create(self.root/"slots.sqlite",self.registration)
        self.addCleanup(self.ledger.close)
        self.source=write_json(self.root/"candidate.json",{"original":"fixture"})
        self.request=f.request("probe",call="producer")
        self.result={"success":True,"contentItems":[{"type":"inputText","text":"original result"}]}
        self.receipt=write_json(self.root/"producer.json",{"source":self.source})
        native=write_json(self.root/"component-native.json",{"fixture":"structural binding tests only"})
        self.binding=write_json(self.root/"binding.json",dict(schema=BINDING_SCHEMA,
            registration_sha256=object_sha(self.registration),operation_id="accept",slot=slot,source=self.source,
            producer_call_id="producer",producer_result=self.receipt,
            producer_native=dict(invocation=native,fence={"component_fixture_only":True}),replacement="fixture exact marker"))

    def producer(self,phase="complete"):
        self.ledger.receive(self.request)
        if phase=="unknown":
            self.ledger.stop("producer","UNKNOWN");return
        self.ledger.operation_result("producer",self.result,self.receipt)
        self.ledger.response_intent("producer")
        message=dict(method="item/completed",params=dict(threadId=self.registration["thread_id"],
            turnId=self.registration["turn_id"],item=dict(id="producer",type="dynamicToolCall",
                namespace="upgrade_collaboration",tool="execute",arguments={"operation_id":"probe"},
                status="completed",**self.result)))
        self.ledger.complete(message,write_json(self.root/"completed.json",message))

    def test_missing_unknown_or_phase_only_producer_cannot_bind(self):
        with self.assertRaisesRegex(ContractError,"PRODUCER_NOT_COMPLETED"):
            self.ledger.bind_artifact("accept",self.binding)
        self.producer("unknown")
        with self.assertRaisesRegex(ContractError,"PRODUCER_NOT_COMPLETED"):
            self.ledger.bind_artifact("accept",self.binding)
        self.ledger.db.execute("UPDATE calls SET phase='NATIVE_COMPLETED',result=?,receipt=?",
                               (canonical(self.result),canonical(self.receipt)))
        self.ledger.db.commit()
        with self.assertRaisesRegex(ContractError,"PRODUCER_HISTORY_CHANGED"):
            self.ledger.bind_artifact("accept",self.binding)
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM bindings").fetchone()[0],0)

    def test_two_controllers_cannot_bind_twice(self):
        self.producer()
        ctx=multiprocessing.get_context("spawn");queue=ctx.Queue()
        workers=[ctx.Process(target=contender,args=(self.ledger.path,object_sha(self.registration),self.binding,queue))
                 for _ in range(2)]
        try:
            for worker in workers:worker.start()
            for worker in workers:worker.join(10);self.assertEqual(worker.exitcode,0)
            self.assertEqual(sorted([queue.get(timeout=2),queue.get(timeout=2)]),["bound","refused"])
        finally:
            for worker in workers:
                if worker.is_alive():worker.terminate();worker.join(5)
            queue.close();queue.join_thread()
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM bindings").fetchone()[0],1)

    def test_bound_operation_unknown_never_replays_or_chooses_other_branch(self):
        self.producer();self.ledger.bind_artifact("accept",self.binding)
        req=self.f.request("accept",call="accept-once")
        self.assertEqual(self.ledger.receive(req)["payload"],{"marker":"fixture exact marker"})
        self.ledger.stop("accept-once","UNKNOWN")
        reopened=OperationLedger(self.ledger.path,object_sha(self.registration))
        self.addCleanup(reopened.close)
        for op in ("accept","reject"):
            with self.assertRaisesRegex(ContractError,"CHOICE_ALREADY_INTENDED_NO_REPLAY"):
                reopened.receive(self.f.request(op))
        with self.assertRaises(ContractError):reopened.bind_artifact("accept",self.binding)
        self.assertEqual(reopened.db.execute("SELECT count(*) FROM calls").fetchone()[0],2)

    def test_rewritten_binding_source_authority_and_database_registration_refuse(self):
        self.producer();self.ledger.bind_artifact("accept",self.binding)
        path=self.root/"binding.json";raw=path.read_bytes();path.write_bytes(raw+b"\n")
        with self.assertRaises(ContractError):self.ledger.receive(self.f.request("accept"))
        path.write_bytes(raw)
        source=self.root/"candidate.json";original=source.read_bytes();source.write_bytes(original+b"\n")
        with self.assertRaises(ContractError):self.ledger.receive(self.f.request("accept"))
        source.write_bytes(original)
        changed=copy.deepcopy(self.registration);changed["operations"]["accept"]["payload"]["marker"]="changed"
        self.ledger.db.execute("UPDATE registration SET body=?",(canonical(changed),));self.ledger.db.commit()
        with self.assertRaisesRegex(ContractError,"REGISTRATION_CHANGED"):
            self.ledger.receive(self.f.request("accept"))
        self.ledger.db.execute("UPDATE registration SET body=?",(canonical(self.registration),));self.ledger.db.commit()
        (self.root/"authority.json").write_text("revoked")
        with self.assertRaises(ContractError):self.ledger.receive(self.f.request("accept"))
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0],1)

    def test_legacy_registration_cannot_gain_generated_slots(self):
        with self.assertRaisesRegex(ContractError,"EXPLICIT_V3_REQUIRED"):
            self.f.ledger.bind_artifact("accept",self.binding)


if __name__=="__main__":unittest.main()
