"""One external owner from original task bootstrap through campaign shutdown.

Explicit construction starts only the read-only observer. Bootstrap is a
separate one-shot action; adoption and business attachment retain their full
gates. The same RPC and readiness ledger survive the handoff. A yielded call
does not close them. A failed owner cannot be reconstructed or restarted.
"""
import os
import copy
from pathlib import Path

from orchestration_common import file_ref, json_ref, require, write_json
from upgrade_task_streams import execution_owner


class TaskLifecycle:
    def __init__(self, runtime_ref, client, *, observer=None):
        from upgrade_live_control import external_publisher
        from upgrade_live_runtime import validate_live_runtime
        from upgrade_startup_contract import TASK_SCHEMA, validate_startup
        from upgrade_task_network import OBSERVER_SCHEMA
        from upgrade_rpc_peer import verify_peer
        _, self.runtime = validate_live_runtime(runtime_ref)
        self.spec, binding = validate_startup(self.runtime['startup'])
        self.binding = copy.deepcopy(binding)
        require(self.spec['schema'] == TASK_SCHEMA
                and binding.get('task_model_network', {}).get('schema') == OBSERVER_SCHEMA,
                'TASK_LIFECYCLE_EXPLICIT_OBSERVER_CONTRACT_REQUIRED')
        external_publisher(self.spec)
        require(not os.path.lexists(self.spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
        require(callable(getattr(client, 'poll_recorded', None))
                and callable(getattr(client, 'receive_recorded', None)),
                'TASK_LIFECYCLE_RETAINED_RPC_REQUIRED')
        verify_peer(client.sock, client.server, client.process)
        self.runtime_ref = copy.deepcopy(runtime_ref)
        self.client = client
        self.pid = os.getpid()
        self.execution = execution_owner()
        self.root = Path(self.spec['adoption_path']).parent / 'task-lifecycle'
        self.root.mkdir(mode=0o700, exist_ok=False)
        self.observer = self.readiness = self.campaign = None
        self.closed = False
        self.phase = 'OBSERVER'
        self.sequence = 0
        self.close_result = None
        self.proof = write_json(self.root/'owner.json', dict(
            schema='upgrade-task-lifecycle-v1', runtime=runtime_ref,
            startup=self.runtime['startup'], owner_pid=self.pid,
            server=client.server, reconnect_allowed=False, input_operations=0), exclusive=True)
        try:
            from upgrade_task_observer import ReadOnlyObserver, start_observer
            if observer is not None:
                require(type(observer) is ReadOnlyObserver and not observer.closed
                        and observer.pid == self.pid and observer.thread is not None
                        and observer.thread.is_alive(), 'TASK_LIFECYCLE_ORIGINAL_OBSERVER_REQUIRED')
                observer.source.verify()
            self.observer = observer if observer is not None else start_observer(runtime_ref)
            require(self.observer.source.runtime_ref == self.runtime_ref,
                    'TASK_LIFECYCLE_OBSERVER_RUNTIME_CHANGED')
            write_json(self.root/'observer.json', dict(owner=self.proof,
                descriptor=self.observer.descriptor, input_operations=0), exclusive=True)
        except BaseException as exc:
            self._failed(exc)
            raise

    def _owner(self):
        require(os.getpid() == self.pid and execution_owner() == self.execution,
                'TASK_LIFECYCLE_EXECUTION_OWNER_CHANGED')

    def _verify(self):
        self._owner()
        require(not self.closed, 'TASK_LIFECYCLE_CLOSED_NO_REPLAY')
        require(file_ref(self.root/'owner.json') == self.proof,
                'TASK_LIFECYCLE_ORIGINAL_OWNER_CHANGED')
        require(json_ref(self.proof)['server'] == self.client.server,
                'TASK_LIFECYCLE_ORIGINAL_RPC_CHANGED')
        from upgrade_rpc_peer import verify_peer
        verify_peer(self.client.sock, self.client.server, self.client.process)
        require(not os.path.lexists(self.spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
        require(self.observer is not None and not self.observer.closed
                and self.observer.thread is not None and self.observer.thread.is_alive(),
                'TASK_LIFECYCLE_OBSERVER_UNAVAILABLE')
        self.observer.source.verify()

    def _failed(self, exc):
        # Keep the original error even if cleanup or recording also fails.
        try:
            write_json(self.root/'unknown.json', dict(phase=self.phase,
                error_type=type(exc).__name__, error=str(exc), retry_allowed=False), exclusive=True)
        except (OSError, RuntimeError, ValueError):
            pass
        try:
            self.close()
        except (OSError, RuntimeError, ValueError):
            pass

    def bootstrap(self):
        self._owner()
        require(self.phase == 'OBSERVER' and not self.closed,
                'TASK_LIFECYCLE_BOOTSTRAP_ALREADY_ATTEMPTED')
        try:
            self._verify()
            write_json(self.root/'bootstrap-intent.json', dict(owner=self.proof,
                maximum_attempts=1, retry_allowed=False), exclusive=True)
            self.phase = 'BOOTSTRAP_INTENDED'
            from upgrade_live_control import bootstrap_task_thread
            result = bootstrap_task_thread(self.runtime_ref, self.client)
            write_json(self.root/'bootstrap-result.json', result, exclusive=True)
            self.phase = 'WAITING_TUI'
            return result
        except BaseException as exc:
            self._failed(exc)
            raise

    def attach_readiness(self):
        """Call after the original display attached; never poll by reenrolling."""
        self._owner()
        require(self.phase == 'WAITING_TUI' and self.readiness is None,
                'TASK_LIFECYCLE_READINESS_ALREADY_ATTEMPTED')
        try:
            self._verify()
            write_json(self.root/'readiness-intent.json', dict(owner=self.proof), exclusive=True)
            self.phase = 'READINESS_INTENDED'
            from upgrade_live_control import readiness_task_actor
            from upgrade_task_actor import TaskService
            actor = readiness_task_actor(self.runtime_ref, self.client)
            # Retain actor ownership even when wrapping fails, for cleanup.
            self.readiness_actor = actor
            self.readiness = TaskService(actor)
            self.phase = 'READINESS'
        except BaseException as exc:
            self._failed(exc)
            raise

    def poll_readiness(self, *, seconds=1, max_messages=64):
        self._owner()
        require(self.phase == 'READINESS', 'TASK_LIFECYCLE_READINESS_NOT_ACTIVE')
        try:
            self._verify()
            result = self.readiness.pulse(seconds=seconds, max_messages=max_messages)
            if self.readiness.actor.closed:
                previous = self.readiness._normal_terminal()
                write_json(self.root/'readiness-completed.json', dict(owner=self.proof,
                    previous_completion=previous,
                    turn_id=self.readiness.actor.ledger.active_turn,
                    adoption_proven=False), exclusive=True)
                self.phase = 'AWAITING_ADOPTION'
            return dict(result, lifecycle_phase=self.phase)
        except BaseException as exc:
            self._failed(exc)
            raise

    def adopt(self):
        """Original external publication gate; NOT_READY keeps this owner alive."""
        self._owner()
        require(self.phase == 'AWAITING_ADOPTION', 'TASK_LIFECYCLE_READINESS_REQUIRED')
        try:
            self._verify()
            from upgrade_live_control import control
            result = control(self.runtime_ref, 'adopt')
            self.sequence += 1
            write_json(self.root/f'adoption-{self.sequence:06d}.json', result, exclusive=True)
            if result['status'] in ('ADOPTED', 'ALREADY_ADOPTED'):
                self.phase = 'ADOPTED'
            else:
                require(result['status'] == 'NOT_READY', 'TASK_LIFECYCLE_ADOPTION_RESULT_CHANGED')
            return result
        except BaseException as exc:
            self._failed(exc)
            raise

    def attach_campaign(self, campaign_ref, *, advice_connections=None):
        self._owner()
        require(self.phase == 'ADOPTED' and self.campaign is None,
                'TASK_LIFECYCLE_ORIGINAL_ADOPTION_REQUIRED')
        try:
            self._verify()
            old = self.readiness
            require(old.actor.scope == frozenset({'ready'}),
                    'TASK_LIFECYCLE_ORIGINAL_READINESS_SCOPE_REQUIRED')
            previous = old._normal_terminal()
            write_json(self.root/'campaign-intent.json', dict(owner=self.proof,
                campaign=campaign_ref, previous_completion=previous,
                ledger_identity=list(old.actor.ledger.identity), retry_allowed=False), exclusive=True)
            self.phase = 'CAMPAIGN_INTENDED'
            from upgrade_live_control import campaign_task_runtime
            self.campaign = campaign_task_runtime(self.runtime_ref, campaign_ref,
                self.client, old.actor.ledger, advice_connections=advice_connections)
            new = self.campaign.service
            require(new.actor.client is old.actor.client is self.client
                    and new.actor.ledger is old.actor.ledger
                    and 'ready' not in new.actor.scope
                    and old.actor.closed and not old.pending,
                    'TASK_LIFECYCLE_HANDOFF_OWNER_CHANGED')
            # The readiness turn already ended. The first business input must
            # follow the original normal-turn grant, not wait on a dead turn.
            new.actor.closed = True
            new.normal_completion = old.normal_completion
            require(new._normal_terminal() == previous, 'TASK_LIFECYCLE_HANDOFF_TERMINAL_CHANGED')
            message_permission=self.binding.get('task_model_network',{}).get('registered_messages') is True
            if message_permission:
                self.campaign.enable_callback_ingress(allow_busy=True,allow_messages=True)
            else:
                self.campaign.enable_callback_ingress(allow_busy=True)
            write_json(self.root/'campaign-result.json', dict(owner=self.proof,
                runtime_owner=self.campaign.proof, callback=self.campaign.callback_ingress.proof,
                original_turn_id=new.actor.ledger.active_turn,
                operation_budget_reset=False, model_inputs_sent=0), exclusive=True)
            self.phase = 'CAMPAIGN'
            return self.campaign.proof
        except BaseException as exc:
            self._failed(exc)
            raise

    def run(self, **limits):
        self._owner()
        require(self.phase == 'CAMPAIGN', 'TASK_LIFECYCLE_CAMPAIGN_NOT_ATTACHED')
        try:
            self._verify()
            result = self.campaign.run(**limits)
            if self.campaign.closed:
                self.close()
            return result
        except BaseException as exc:
            self._failed(exc)
            raise

    def close(self):
        """Release owned resources, preserving original UNKNOWN and evidence."""
        self._owner()
        if self.closed:
            return self.close_result
        self.closed = True
        errors = []
        actors = [getattr(self, 'readiness_actor', None)]
        if self.campaign is not None:
            self.campaign.closed = True
            actors.append(self.campaign.actor)
            if self.campaign.callback_ingress is not None:
                try:
                    self.campaign.callback_ingress.close()
                except Exception as exc:
                    errors.append(str(exc))
        ledgers = {}
        for actor in actors:
            if actor is None:
                continue
            ledgers[id(actor.ledger)] = actor.ledger
            try:
                if not actor.closed:
                    actor.close('UNKNOWN')
            except Exception as exc:
                errors.append(str(exc))
        for ledger in ledgers.values():
            try:
                ledger.close()
            except Exception as exc:
                errors.append(str(exc))
        observer = None
        if self.observer is not None:
            try:
                observer = self.observer.stop()
            except Exception as exc:
                errors.append(str(exc))
        try:
            self.client.close()
        except Exception as exc:
            errors.append(str(exc))
        self.close_result = dict(status='CLOSED', phase=self.phase, observer=observer,
            cleanup_errors=errors, model_inputs_sent=0, reconnect_allowed=False,
            business_accepted=False)
        write_json(self.root/'closed.json', self.close_result, exclusive=True)
        return self.close_result

    def __enter__(self):
        self._verify()
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc is None:
            self.close()
        else:
            self._failed(exc)
        return False
