"""Cross-turn budgets and route choices persist in one original ledger."""
import copy
import os
import unittest
import uuid

from orchestration_common import ContractError, object_sha, write_json
from upgrade_task_operations import OperationLedger
import test_upgrade_task_operations as fixtures


class TaskTurnTests(unittest.TestCase):
    def setUp(self):
        f = fixtures.OperationTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.f = f
        self.server = dict(pid=os.getpid(), uid=os.getuid(), birth=[123,456],
            executable="/fixture/codex", file_identity=[1,2,3,4], argv_sha256="a"*64)
        self.registration = dict(f.registration, schema="upgrade-task-operations-v2", server=self.server,
            initial_operations=["probe"], choice_groups={"route":["left","right"]},
            operations={**f.registration["operations"], "left":dict(kind="route",payload={"target":"A"}),
                        "right":dict(kind="route",payload={"target":"B"})})
        self.ledger = OperationLedger.create(f.root/"multi.sqlite",self.registration)
        self.addCleanup(self.ledger.close)
        self.serial = 0

    def record(self, label, value):
        self.serial += 1
        return write_json(self.f.root/(str(self.serial)+"-"+label+".json"),value,exclusive=True)

    def grant(self, ops, *, previous=None, turn=None, server=None):
        previous = previous or self.ledger.active_turn
        turn = turn or str(uuid.uuid4())
        server = server or self.server
        peer = dict(schema="upgrade-task-rpc-peer-v1",server=server,
                    peer={k:server[k] for k in ("pid","uid")})
        def rpc(direction,message):return self.record("rpc",dict(direction=direction,message=message,peer=peer))
        value = dict(schema="upgrade-task-operation-turn-v1",registration_sha256=object_sha(self.registration),
            authority_ref=self.registration["authority_ref"],previous_turn_id=previous,turn_id=turn,operation_ids=ops,
            previous_completion=rpc("received",dict(method="turn/completed",params=dict(
                threadId=self.registration["thread_id"],turn=dict(id=previous,status="completed")))),
            turn_start=dict(intent=rpc("send_intent",dict(id=100,method="turn/start",params=dict(
                threadId=self.registration["thread_id"],input=[dict(type="text",text="next fixed instruction")]))),
                response=rpc("received",dict(id=100,result=dict(turn=dict(id=turn))))))
        return self.record("grant",value), turn

    def request(self, op, turn=None, call=None):
        value = copy.deepcopy(self.f.request)
        self.serial += 1
        value["id"]=self.serial
        value["params"].update(turnId=turn or self.ledger.active_turn,callId=call or "call-"+str(self.serial),
                               arguments={"operation_id":op})
        return value

    def test_unknown_first_turn_never_reexecutes_in_later_turn_or_after_reopen(self):
        req=self.request("probe")
        self.ledger.receive(req);self.ledger.stop(req["params"]["callId"],"UNKNOWN")
        grant,turn=self.grant(["probe","other"])
        self.assertFalse(self.ledger.enroll_turn(grant)["operation_budget_reset"])
        reopened=OperationLedger(self.ledger.path,object_sha(self.registration))
        try:
            with self.assertRaisesRegex(ContractError,"NO_REPLAY"):
                reopened.receive(self.request("probe",turn))
            self.assertEqual(reopened.receive(self.request("other",turn))["kind"],"record_probe")
        finally:reopened.close()

    def test_closed_turn_and_not_yet_enrolled_turn_cannot_execute(self):
        old=self.ledger.active_turn
        grant,turn=self.grant(["other"])
        with self.assertRaisesRegex(ContractError,"FOREIGN_TURN"):
            self.ledger.receive(self.request("other",turn))
        self.ledger.enroll_turn(grant)
        with self.assertRaisesRegex(ContractError,"FOREIGN_TURN"):
            self.ledger.receive(self.request("probe",old))
        with self.assertRaisesRegex(ContractError,"OUTSIDE_TURN_SCOPE"):
            self.ledger.receive(self.request("probe",turn))

    def test_unsealed_pending_request_blocks_transition(self):
        self.ledger.receive(self.request("probe"))
        grant,_=self.grant(["other"])
        with self.assertRaisesRegex(ContractError,"PENDING_MUST_BE_SEALED"):
            self.ledger.enroll_turn(grant)

    def test_mutually_exclusive_route_keeps_global_budget_after_unknown(self):
        grant,_=self.grant(["left","right"]);self.ledger.enroll_turn(grant)
        request=self.request("left");self.ledger.receive(request)
        self.ledger.stop(request["params"]["callId"],"UNKNOWN")
        grant,_=self.grant(["right"]);self.ledger.enroll_turn(grant)
        with self.assertRaisesRegex(ContractError,"CHOICE_ALREADY_INTENDED_NO_REPLAY"):
            self.ledger.receive(self.request("right"))

    def test_replayed_or_reordered_grant_cannot_reset_scope(self):
        first,_=self.grant(["other"])
        stale,_=self.grant(["probe"])
        self.ledger.enroll_turn(first)
        for grant in (first,stale):
            with self.assertRaisesRegex(ContractError,"TURN_CHAIN_CHANGED"):
                self.ledger.enroll_turn(grant)

    def test_foreign_peer_and_authority_change_refuse_new_turn(self):
        grant,_=self.grant(["other"],server=dict(self.server,birth=[999,0]))
        with self.assertRaisesRegex(ContractError,"PEER_CHANGED"):
            self.ledger.enroll_turn(grant)
        grant,_=self.grant(["other"])
        (self.f.root/"authority.json").write_text("changed")
        with self.assertRaises(ContractError):self.ledger.enroll_turn(grant)

    def test_old_v1_ledger_never_gains_multi_turn_authority(self):
        grant,_=self.grant(["other"])
        with self.assertRaisesRegex(ContractError,"LEGACY_TURN_FIXED"):
            self.f.ledger.enroll_turn(grant)

    def test_other_rpc_turn_is_not_accepted(self):
        from orchestration_common import json_ref
        grant,_=self.grant(["other"])
        value=json_ref(grant);value["turn_id"]=str(uuid.uuid4())
        changed=self.record("wrong-turn",value)
        with self.assertRaisesRegex(ContractError,"NEW_TURN_RPC_CHANGED"):
            self.ledger.enroll_turn(changed)

    def test_new_turn_cannot_override_environment_or_execution_policy(self):
        from orchestration_common import json_ref
        for patch in ({"environments":[{"id":"remote"}]},{"approvalPolicy":"never"},
                      {"sandboxPolicy":{"type":"dangerFullAccess"}},{"model":"other-model"}):
            grant,_=self.grant(["other"])
            value=json_ref(grant)
            intent=json_ref(value["turn_start"]["intent"])
            intent["message"]["params"].update(patch)
            value["turn_start"]["intent"]=self.record("overridden-rpc",intent)
            changed=self.record("overridden-grant",value)
            with self.subTest(patch=patch),self.assertRaisesRegex(ContractError,"NEW_TURN_RPC_CHANGED"):
                self.ledger.enroll_turn(changed)


if __name__ == "__main__":unittest.main()
