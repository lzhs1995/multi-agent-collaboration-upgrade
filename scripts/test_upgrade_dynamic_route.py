"""Routing regressions: controller/TUI response races and no replay on failure."""
import copy
import unittest

from orchestration_common import ContractError
from upgrade_dynamic_route import NAMESPACE, TaskToolRoute

THREAD = "11111111-1111-4111-8111-111111111111"
TURN = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


class DynamicRouteTests(unittest.TestCase):
    def setUp(self):
        self.route = TaskToolRoute(THREAD, ["task_status", "submit_result"])
        self.message = {"id": 7, "method": "item/tool/call", "params": {
            "threadId": THREAD, "turnId": TURN, "callId": "call-1", "namespace": NAMESPACE,
            "tool": "task_status", "arguments": {"task": "original"}}}
        self.result = {"success": True, "contentItems": [{"type": "inputText", "text": "ok"}]}

    def test_controller_call_never_forwarded_to_tui_and_tui_cannot_answer_it(self):
        source = copy.deepcopy(self.message)
        action = self.route.from_server(self.message)
        self.assertEqual(action["route"], "controller")
        self.assertFalse(action["caller_authentication"])
        self.assertFalse(action["task_authorization_proven"])
        self.assertEqual(self.message, source)
        for result in ({"id": 7, "result": self.result}, {"id": 7, "error": {"code": -1}}):
            with self.assertRaisesRegex(ContractError, "TUI_RESPONSE_NOT_OWNED"):
                self.route.from_tui(result)
        response = self.route.controller_response(7, self.result)
        self.assertEqual(response["message"]["id"], 7)
        self.assertFalse(response["native_completion_proven"])

    def test_native_tui_tool_and_approval_remain_owned_by_tui(self):
        for number, method in [(1, "item/tool/call"), (2, "item/commandExecution/requestApproval")]:
            value = copy.deepcopy(self.message)
            value.update(id=number, method=method)
            value["params"]["namespace"] = "tasks"
            self.assertEqual(self.route.from_server(value), {"route": "tui", "message": value})
            with self.assertRaisesRegex(ContractError, "CONTROLLER_RESPONSE_NOT_OWNED"):
                self.route.controller_response(number, self.result)
            response = {"id": number, "result": {"original": "tui reply"}}
            self.assertEqual(self.route.from_tui(response), {"route": "server", "message": response})

    def test_notifications_and_directional_id_collision_are_unchanged(self):
        self.route.from_server(self.message)
        # Same numeric ID in client request namespace cannot answer a server call.
        client_request = {"id": 7, "method": "thread/read", "params": {"threadId": THREAD}}
        self.assertEqual(self.route.from_tui(client_request)["message"], client_request)
        server_response = {"id": 7, "result": {"thread": {"id": THREAD}}}
        self.assertEqual(self.route.from_server(server_response)["message"], server_response)
        notification = {"method": "item/started", "params": {"threadId": THREAD}}
        self.assertEqual(self.route.from_server(notification)["message"], notification)

    def test_foreign_child_thread_cannot_inherit_parent_tool_scope(self):
        value = copy.deepcopy(self.message)
        value["params"]["threadId"] = OTHER
        with self.assertRaisesRegex(ContractError, "FOREIGN_THREAD"):
            self.route.from_server(value)
        self.assertEqual(self.route.requests, {})

    def test_duplicate_request_or_call_never_returns_second_task_action(self):
        self.route.from_server(self.message)
        with self.assertRaisesRegex(ContractError, "SERVER_REQUEST_REUSED"):
            self.route.from_server(self.message)
        with self.assertRaisesRegex(ContractError, "CALL_REUSED"):
            self.route.from_server(dict(self.message, id=8))

    def test_unknown_write_and_disconnect_do_not_allow_retry(self):
        self.route.from_server(self.message)
        self.route.controller_response(7, self.result)
        with self.assertRaisesRegex(ContractError, "CONTROLLER_RESPONSE_NOT_OWNED"):
            self.route.controller_response(7, self.result)
        pending = self.route.disconnect()
        self.assertEqual(pending, [{"request_id": 7, "owner": "controller", "state": "UNKNOWN",
                                    "last_local_state": "response_intended"}])
        with self.assertRaisesRegex(ContractError, "CONNECTION_CLOSED_NO_REPLAY"):
            self.route.from_server(self.message)

    def test_string_ids_differ_from_integers_and_boolean_id_is_refused(self):
        self.route.from_server(self.message)
        value = copy.deepcopy(self.message)
        value["id"] = "7"
        value["params"]["callId"] = "call-2"
        self.assertEqual(self.route.from_server(value)["route"], "controller")
        with self.assertRaisesRegex(ContractError, "REQUEST_ID_INVALID"):
            self.route.from_server(dict(value, id=True))

    def test_unowned_result_unknown_tool_bad_schema_and_budget_refuse(self):
        with self.assertRaisesRegex(ContractError, "TUI_RESPONSE_NOT_OWNED"):
            self.route.from_tui({"id": 99, "result": {}})
        for change in ({"tool": "arbitrary_shell"}, {"turnId": "bad"}, {"arguments": "shell command"},
                       {"inheritedRoot": True}):
            value = copy.deepcopy(self.message)
            value["params"].update(change)
            with self.subTest(change=change), self.assertRaises(ContractError):
                self.route.from_server(value)
        route = TaskToolRoute(THREAD, ["task_status"], limit=1)
        route.from_server(self.message)
        with self.assertRaisesRegex(ContractError, "BUDGET_EXHAUSTED"):
            route.from_server(dict(self.message, id=10))


if __name__ == "__main__":
    unittest.main()
