"""Original typed dispatch/receipt attribution with explicit native fixtures."""
import copy
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref,utc_now
from orchestration_verify import _input_units,_human_interventions
from upgrade_input_provenance import ProvenanceJournal,record_key
import test_upgrade_task_reconcile as fixture


class AttributionTests(unittest.TestCase):
    def setUp(self):
        f=fixture.ReconcileTests();f.typed_inputs=True;f.setUp();self.addCleanup(f.doCleanups)
        self.helper=f;self.f=f.f
        self.f.call("reconcile");self.f.complete("reconcile")
        self.assertEqual(f.transport.current()[0].value,"DISPATCHED")
        self.co=self.f.runner.co("run-01");self.registration=self.co.campaign["input_provenance"]["registrations"]["run-01"]
        self.readers={k:c.reader for k,c in self.f.c.clients.items()}
        self.handshake=self.helper.transport.current()[1]["prepared"]["handshake"]["delivery"]

    def classify(self):
        ends={k:r.snapshot() for k,r in self.readers.items()}
        allowed={k:set() for k in self.readers}
        # The actual verified handshake retains its existing protocol exemption.
        proof=json_ref(self.handshake["receipt"])["native_proof"]
        allowed["claude_a"].add(record_key(proof))
        return _input_units(self.co,self.readers,ends,allowed,self.registration,not_after=utc_now())

    def test_original_typed_dispatch_is_machine_and_publish_is_idempotent(self):
        p=self.co.root/"input-provenance.jsonl";before=p.read_bytes()
        from upgrade_task_input_provenance import publish
        publish(self.helper.transport,self.helper.transport.current()[1]["delivery"])
        self.assertEqual(p.read_bytes(),before)
        result,unknown,_=self.classify()
        self.assertEqual(result["counts"]["MACHINE"],1)
        self.assertFalse(unknown);self.assertEqual(_human_interventions(unknown,result),0)
        item=next(v for v in result["units"] if v["provenance"]=="MACHINE")
        expected=json_ref(file_ref(self.f.directory/"dispatch.json"))["native"]
        self.assertEqual(item["producer_call"],expected)

    def test_neighbor_input_keeps_manual_count_unknown(self):
        client=self.f.c.clients["claude_a"]
        neighbor=client.append(client.message("user","unclassified operator input",at=utc_now()))
        result,unknown,_=self.classify()
        self.assertEqual(result["counts"]["MACHINE"],1);self.assertEqual(unknown,[neighbor])
        self.assertIsNone(_human_interventions(unknown,result))

    def test_duplicate_index_cannot_choose_a_winner(self):
        from upgrade_input_provenance import _entries
        p=self.co.root/"input-provenance.jsonl";_,rows=_entries(p.read_bytes(),"run-01")
        journal=ProvenanceJournal(p,"run-01",self.registration)
        body={k:v for k,v in rows[0].items() if k not in ("schema","run_id","seq","previous_sha256")}
        journal._append(body)
        result,unknown,_=self.classify()
        self.assertEqual(result["counts"]["MACHINE"],0);self.assertEqual(len(unknown),1)
        self.assertIsNone(_human_interventions(unknown,result))

    def test_missing_original_actor_never_converts_input_to_machine(self):
        p=self.helper.transport.root/"operations/submit-task-pack/task-actor.json"
        before=p.read_bytes();p.write_bytes(before+b" ")
        result,unknown,_=self.classify()
        self.assertEqual(result["counts"]["MACHINE"],0);self.assertEqual(len(unknown),1)
        self.assertTrue(result["unresolved"])
        p.write_bytes(before)

    def test_registration_must_bind_the_original_supervisor_before_run(self):
        from upgrade_task_input_provenance import validate_campaign_producers
        from upgrade_input_provenance import registered_producers
        producers=registered_producers(self.registration,"run-01")
        validate_campaign_producers(self.co.campaign,producers)
        for transform in (lambda p:p.clear(),lambda p:p.update(duplicate=copy.deepcopy(p["typed-supervisor"])),
                          lambda p:p["typed-supervisor"]["session"].update(session_id="foreign")):
            values=copy.deepcopy(producers);transform(values)
            with self.assertRaisesRegex(ContractError,"ONE_REGISTERED_DISPATCH_PRODUCER_REQUIRED"):
                validate_campaign_producers(self.co.campaign,values)


if __name__=="__main__":unittest.main()
