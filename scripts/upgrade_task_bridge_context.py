"""Explicit trusted-process bridge adapter, separate from foreground identity.

Only the retained external service installs an observer during its exact phase.
Saved provenance cannot activate this context. Existing Claude/legacy callers
continue through the original native guard when no adapter is installed.
"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import os
import threading

from orchestration_common import require

SOURCE="original_task_rpc_and_live_tree"
_active=ContextVar("upgrade_explicit_task_bridge",default=None)


def _owner():
    try:task=asyncio.current_task()
    except RuntimeError:task=None
    return os.getpid(),threading.get_ident(),task


@contextmanager
def activate(observer):
    require(callable(observer) and _active.get() is None,"TASK_BRIDGE_CONTEXT_UNAVAILABLE")
    lease=dict(active=True,owner=_owner())
    token=_active.set((observer,lease))
    try:
        snapshot()
        yield
    finally:
        lease["active"]=False
        _active.reset(token)


def snapshot():
    context=_active.get()
    if context is None:return None
    observer,lease=context
    require(lease["active"] and lease["owner"]==_owner(),"TASK_BRIDGE_OWNER_CHANGED_OR_CLOSED")
    value=observer()
    require(value.get("identity_source")==SOURCE and value.get("foreground_authentication") is False,
            "TASK_BRIDGE_EXPLICIT_SOURCE_REQUIRED")
    return value


def whoami():
    value=snapshot()
    if value is None:return None
    return dict(value["caller"],provider="codex",identity_source=SOURCE,task_actor=value["task_actor"])


def workspace():
    value=snapshot()
    return None if value is None else value["caller"]["workspace_id"]


def list_surfaces(workspace=None):
    value=snapshot()
    if value is None:return None
    caller=value["caller"]
    require(workspace in (None,caller["workspace_id"],caller["workspace_ref"]),
            "TASK_BRIDGE_FOREIGN_WORKSPACE")
    # Inventory remains the actual workspace tree. Discovery is not permission
    # to address a surface: resolve() restricts the exact registered lane.
    rows=[ws for win in value["tree"]["windows"] for ws in win.get("workspaces",[])
          if ws.get("id","").upper()==caller["workspace_id"].upper()]
    require(len(rows)==1,"TASK_BRIDGE_WORKSPACE_AMBIGUOUS")
    ws=rows[0]
    return [dict(ref=s["ref"],surface_id=s["id"],workspace_id=ws["id"],workspace_ref=ws["ref"],
                 pane_ref=pane.get("ref",""),title=s.get("title",""),
                 selected=s["id"].upper()==caller["surface_id"].upper(),
                 surface_type=s.get("type","unknown"),is_terminal=s.get("type")=="terminal")
            for pane in ws.get("panes",[]) for s in pane.get("surfaces",[])
            if s.get("dock_scope")!="global"]


def resolve(target,*,expected=None,resolver):
    value=snapshot()
    if value is None:return None
    # This is the registered task's display endpoint, explicitly not a native
    # foreground proof. No environment values or collector are substituted.
    proof=resolver(dict(caller=value["caller"]),value["tree"],target,expected=expected)
    require(proof["target_surface_uuid"].upper()==value["target"]["surface_uuid"].upper(),
            "TASK_BRIDGE_TARGET_OUTSIDE_OPERATION")
    proof.update(identity_source=SOURCE,task_actor=value["task_actor"])
    return proof


def participants(workspace,rows):
    value=snapshot()
    if value is None:return None
    caller=value["caller"];sender=value["supervisor"];target=value["target"]
    require(workspace.upper()==caller["workspace_id"].upper() and len(rows)==2,
            "TASK_BRIDGE_PARTICIPANT_SCOPE_CHANGED")
    result=[]
    for row,role,session in zip(rows,("supervisor","executor"),(sender,target)):
        require(set(row)=={"role","surface_uuid","provider"} and row["role"]==role
                and row["surface_uuid"].upper()==session["surface_uuid"].upper()
                and row["provider"]==session["provider"],"TASK_BRIDGE_PARTICIPANT_SCOPE_CHANGED")
        result.append(dict(row,native_session_id=session["session_id"],identity_source=SOURCE,
                           task_actor=value["task_actor"]))
    return result


def provenance():
    value=snapshot()
    return None if value is None else value["task_actor"]
