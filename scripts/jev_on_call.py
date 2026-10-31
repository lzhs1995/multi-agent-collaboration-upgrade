"""Production on-arm transport for the JEV advisory worker: S1's own wire protocol.

Reuses the frozen S1 design in
/Users/lzhs/uz/state/B/recovery-20261024/jev-validation-20261025-r1/jev_adapter.py
(``JevClient.ask``, ``payload``, ``validate_response``): one persistent HTTPS
connection reused for every request, ``POST /v1/systemone`` with a bearer
credential, the S1 v2 question wording copied verbatim, and no automatic retry.
``http.client`` never retries, so one call is exactly one attempt; a 401/403
comes back as its status for ``jev_transport`` to stop on before parsing. S1
did not use the TypeSafe SDK and it is not installed here; a later SDK caller
must build one client with ``TypeSafeClient(retry=RetryPolicy(max_retries=0))``
(SDK v0.7.5) and reuse it.

Nothing is read from a file or an environment variable. ``bind`` needs a real
authorization and a valid credential held by its caller; offline tests pass a
stand-in connection and a placeholder string and make no request.
"""
from __future__ import annotations

import json
import re

HOST, PATH, TIMEOUT_SECONDS = "api.typesafe.ai", "/v1/systemone", 12
# Credential *shapes*, not substrings: S1's bare "sk-" test refused ordinary
# words such as "task-routing" (R491). A key-like token must follow the prefix,
# or a credential field must carry a value; the caller's own credential is also
# refused verbatim. The credential travels only in the Authorization header.
CREDENTIAL_SHAPE = re.compile(
    r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|(?<![A-Za-z0-9])(?:sk|apikey)[-_][A-Za-z0-9]{16,}"
    r"|(?i:\b(?:api[_-]?key|access[_-]?token|secret[_-]?key)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._-]{8,})")
WORK = {
    "coding": "Implement new software behavior or change code, without an observed defect to diagnose.",
    "debugging": "Investigate or repair an observed software failure, error, crash or incorrect behavior.",
    "review": "Independently inspect existing code, tests or evidence; do not implement changes.",
    "literature": "Find, read or compare scholarly sources and check source support for claims.",
}
QUESTIONS = {
    "work_kind": {"type": "choice", "instructions": "What kind of work is requested in `task`?", "criteria": WORK},
    "work_kind_applies": {
        "type": "noul",
        "instructions": "Does `task` request an uncompleted work item in the supported triage scope?",
        "criteria": {
            "true": "A concrete coding, debugging, independent review or scholarly-source work item is requested.",
            "false": "Only an acknowledgment, completed work, vague discussion or an administrative operation outside these work kinds is described.",
        },
    },
}


def connect(timeout=TIMEOUT_SECONDS):
    """The one connection a caller builds and reuses; nothing connects until a request."""
    import http.client
    import ssl
    return http.client.HTTPSConnection(HOST, timeout=timeout, context=ssl.create_default_context())


def wire_payload(request: dict, credential: str | None = None) -> dict:
    """S1's ``payload``: only task and message leave the process, never a credential."""
    if (request["question"]["id"] != "work_kind" or set(request["question"]["options"]) != set(WORK)
            or request["companion"]["id"] != "work_kind_applies"):
        raise ValueError("the worker question no longer matches the S1 wire questions")
    state = dict(task=request["state"], message="")
    text = json.dumps(state, ensure_ascii=False)
    if CREDENTIAL_SHAPE.search(text) or (credential and credential in text):
        raise ValueError("credential-like state is not allowed")
    return dict(model=request["model"], state=state, questions=QUESTIONS)


def bind(connection, credential: str, *, model: str):
    """One send/parse pair over the caller's reused ``connection``."""
    if connection is None or not isinstance(credential, str) or not credential:
        raise ValueError("a caller-held connection and credential are required")

    def send(request: dict) -> dict:
        payload = wire_payload(dict(request, model=model), credential)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        connection.request("POST", PATH, encoded, {"Authorization": "Bearer " + credential,
                                                   "Content-Type": "application/json"})
        response = connection.getresponse()
        return dict(http_status=response.status, body=response.read().decode())

    def parse(response: dict) -> dict:
        """S1's ``validate_response`` for the two questions asked, then the worker's shape."""
        if response["http_status"] != 200:
            return dict(answers=None, error="HTTP_%d" % response["http_status"], usage=None)
        value = json.loads(response["body"])
        if not isinstance(value, dict) or value.get("model") != model:
            raise ValueError("missing or changed model provenance")
        answers = value.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(QUESTIONS):
            raise ValueError("question/answer keys do not match exactly")
        kind, applies = answers["work_kind"], answers["work_kind_applies"]
        probabilities = kind.get("probabilities") if isinstance(kind, dict) else None
        if (not isinstance(probabilities, dict) or set(probabilities) != set(WORK) or kind.get("type") != "choice"
                or kind.get("choice") not in WORK
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in probabilities.values())
                or abs(sum(probabilities.values()) - 1) > 0.05):
            raise ValueError("invalid choice or probability keys")
        if not isinstance(applies, dict) or applies.get("type") != "noul":
            raise ValueError("wrong answer type")
        usage = value.get("usage")
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                                              for k in ("input_tokens", "output_tokens")):
            raise ValueError("invalid token usage")
        return dict(answers=dict(work_kind=kind["choice"], work_kind_applies=applies.get("noul")), error=None,
                    usage=usage)

    return send, parse
