"""External candidate verification in its own frozen private import scope.

The driver may already have another controller imported. Never remove or
replace its modules, especially while its observer is running. The child only
reads the candidate and original evidence; publication stays with the caller.
"""
from pathlib import Path
import subprocess

from orchestration_common import ContractError, canonical, object_sha, require, strict_json
from upgrade_client_isolation import clean_child_environment

CHECK_CODE = '''import json,sys
sys.path.insert(0,sys.argv[1])
from mac_upgrade_task_adoption import check_request
from mac_upgrade_common import object_sha,strict_json
try:
 value=check_request(strict_json(sys.stdin.buffer.read(),'adoption candidate'))
 result=dict(status='VERIFIED',proof_sha256=object_sha(value),input_operations=0)
except Exception as exc:
 result=dict(status='REFUSED',error=str(exc),input_operations=0)
print(json.dumps(result,sort_keys=True))
'''


def verify_candidate(runtime_ref, runtime, spec, proof, *, allow_stopped=False):
    scripts = Path(runtime['controller']['path'])/'scripts'
    argv = ['/usr/bin/sandbox-exec', '-f', spec['pins']['control_sandbox']['path'],
            runtime['python']['path'], '-I', '-B', '-c', CHECK_CODE, str(scripts)]
    try:
        result = subprocess.run(argv, input=canonical(dict(runtime=runtime_ref, proof=proof,
            allow_stopped=allow_stopped)), capture_output=True, timeout=60,
            cwd=spec['work_root'], env=clean_child_environment({}), close_fds=True)
    except subprocess.TimeoutExpired as exc:
        raise ContractError('TASK_ADOPTION_ISOLATED_CHECK_TIMEOUT') from exc
    require(result.returncode == 0, 'TASK_ADOPTION_ISOLATED_CHECK_FAILED')
    value = strict_json(result.stdout, 'isolated adoption check')
    require(value.get('status') == 'VERIFIED',
            'TASK_ADOPTION_ISOLATED_CHECK_REFUSED:'+str(value.get('error', 'invalid result')))
    require(value == dict(status='VERIFIED', proof_sha256=object_sha(proof), input_operations=0),
            'TASK_ADOPTION_ISOLATED_RESULT_CHANGED')
    return proof
