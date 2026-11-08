"""Historical busy-callback proof; no caller or replay capability."""
from pathlib import Path
import sqlite3

from orchestration_common import file_ref,json_ref,object_sha,require,strict_json


def verify_busy_input(binding,request,attempt,route,endpoint,details,*,message=False):
    label='message' if message else 'callback'
    source_key='source' if message else 'pack'
    key=object_sha({source_key:route[source_key],'attempt':route['attempt']})
    root=Path(endpoint['registration']['path']).parent/'campaign-actor-events'/(label+'-steers')/key
    ref=file_ref(root/'result.json');value=json_ref(ref)
    require(value['schema']=='upgrade-task-busy-'+label+'-result-v1'
            and value['source']['path']==str(root/'source.json')
            and value['intent']['path']==str(root/'input-intent.json'),
            'TASK_BUSY_CALLBACK_ORIGINAL_RESULT_REQUIRED')
    source=json_ref(value['source']);intent=json_ref(value['intent'])
    snapshot=json_ref(source['original_attempt_snapshot']);original=source['original']
    registration=json_ref(endpoint['registration']);campaign=json_ref(endpoint['campaign'])
    enrollment=json_ref(endpoint['enrollment']);turn=request['observed_turn_id']
    require(source['schema']=='upgrade-task-busy-'+label+'-source-v1'
            and source['enrollment']==endpoint['enrollment']
            and source['owner_pid']==endpoint['owner']['pid']==enrollment['controller_pid']
            and source['original_attempt_snapshot']['path']==str(root/'attempt.json')
            and original[source_key]==route[source_key] and original['attempt']['path']==route['attempt']
            and all(original['attempt'][k]==source['original_attempt_snapshot'][k] for k in ('bytes','sha256'))
            and original['request']==request and original['text']==details['text']
            and snapshot['native_binding']==binding==attempt['native_binding']
            and snapshot['binding']==attempt['binding'] and snapshot['phase']=='NATIVE_INPUT_INTENT'
            and len(snapshot['events'])==1 and snapshot['events']==attempt['events'][:1]
            and snapshot['events'][0]['native_request']==request
            and intent==dict(source=value['source'],method='turn/steer',params=request['params'],
                maximum_inputs=1,retry_allowed=False)
            and source['ledger_turn']==value['ledger_turn']
            and value['ledger_turn']['turn_id']==turn
            and value['ledger_turn']['registration_sha256']==object_sha(registration)
            and value['native_user_receipt'] is False and value['model_consumption'] is False
            and source['input_operations']==0
            and source['native_user_receipt'] is False and source['model_consumption'] is False,
            'TASK_BUSY_CALLBACK_ORIGINAL_SOURCE_CHANGED')
    sender=campaign['sessions'][original['lane']]
    ancestry=original['caller']['ancestry']
    if message:
        from upgrade_task_message_route import verify_registered_source
        verify_registered_source(original,route,endpoint)
    require(sender['provider']=='claude' and sender['surface_uuid'].upper()==details['sender_surface'].upper()
            and len(ancestry)>1 and original['caller']['peer']['pid']==ancestry[0]['pid']
            and original['caller']['peer']['uid']==registration['server']['uid']
            and all(ancestry[-1][k]==sender['process'][k] for k in ('pid','birth','executable','file_identity'))
            and all(a['ppid']==b['pid'] for a,b in zip(ancestry,ancestry[1:])),
            'TASK_BUSY_CALLBACK_ORIGINAL_CALLER_CHANGED')
    from upgrade_task_creation import exchange
    sent,received=exchange(value['exchange'],'turn/steer',registration['server'])
    require(sent==request['params'] and received.get('turnId')==turn
            and value['result']==dict(status='ACTIVE',turn_id=turn,model_inputs_sent=1,
                operation_budget_reset=False,operation_scope_changed=False),
            'TASK_BUSY_CALLBACK_ORIGINAL_EXCHANGE_CHANGED')
    path=Path(enrollment['ledger']['path']);st=path.stat()
    require((st.st_dev,st.st_ino)==(enrollment['ledger']['device'],enrollment['ledger']['inode']),
            'TASK_BUSY_CALLBACK_LEDGER_CHANGED')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        db.execute('BEGIN')
        row=db.execute('SELECT sequence,scope,grant FROM turns WHERE turn_id=?',(turn,)).fetchone()
        reg=db.execute('SELECT body FROM registration WHERE id=1').fetchone()
        require(reg and strict_json(reg[0])==registration and row
                and value['ledger_turn']==dict(turn_id=turn,sequence=row[0],scope=strict_json(row[1]),
                    grant=None if row[2] is None else strict_json(row[2]),registration_sha256=object_sha(registration)),
                'TASK_BUSY_CALLBACK_ORIGINAL_TURN_CHANGED')
    finally:db.close()
    fresh=path.stat()
    require((fresh.st_dev,fresh.st_ino)==(st.st_dev,st.st_ino),'TASK_BUSY_CALLBACK_LEDGER_CHANGED')
    return ref
