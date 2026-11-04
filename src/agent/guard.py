"""
The data guard: what a tool result may show the model. A local (self-hosted) model sees everything. A cloud model never sees a result
labelled restricted. In facility mode (anything restricted declared: a restricted pack or folder, or the workspace's mode: facility;
automc_settings) it sees a result only when every label in it is public, and never an unlabelled one. In open mode (nothing restricted
declared, so nothing restricted is reachable) it sees every result not labelled restricted (devops/AutoMC_Workspace_Plan.md, WD-2).
Everything withheld is replaced by a short note for the model, and the user is told.

Labels are the `facility_data` fields the tools put in their results (presets.facility_data, the job service, the run reader): a
mapping with "distribution", or the label itself as a string. A null label means unknown, which in facility mode counts as restricted
(Block 5 plan, section 5b: everything not clearly public is restricted).
"""
from __future__ import annotations

import json

import automc_settings

LABEL_KEY = "facility_data"
NOT_PUBLIC = frozenset({"restricted", "unknown"})


def labels(obj):
    """Every facility label found anywhere in a tool result."""
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == LABEL_KEY:
                    if isinstance(v, dict):
                        found.add(v.get("distribution") or "unknown")
                    elif isinstance(v, str):
                        found.add(v)
                    elif v is None:
                        found.add("unknown")
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(obj)
    return found


def restricted_pack_installed():
    return automc_settings.restricted_pack_installed()


def facility_mode():
    """Anything restricted is declared (a restricted pack or folder, or the workspace's mode: facility); automc_settings."""
    return automc_settings.declared_facility() or restricted_pack_installed()


def holds_restricted(result):
    """True if a tool result carries restricted (or unknown) facility data."""
    return bool(labels(result) & NOT_PUBLIC)


def check(result, cloud, policy_ok=False):
    """(allowed, reason). `cloud` is whether the model runs outside this machine; `policy_ok` is the data owners' two-key decision for
    this provider (llm.restricted_allowed), which lets restricted results through to it."""
    if not cloud or policy_ok:
        return True, None
    found = labels(result)
    if "restricted" in found:
        return False, "it holds facility data labelled restricted"
    if not facility_mode():                             # open mode: nothing restricted is reachable
        return True, None
    if found & NOT_PUBLIC:
        return False, f"it holds facility data labelled {', '.join(sorted(found & NOT_PUBLIC))}"
    if not found:
        return False, "it carries no public label while something restricted is declared (facility mode)"
    return True, None


def entered_label(result, cloud, policy_ok=False):
    """The label of what a tool result puts into the conversation: public, unknown or restricted. A withheld result puts in only a
    note, so it is public. An allowed result is judged by the cloud rule (as if it were going to a cloud model now): public only if a
    cloud model could have seen it."""
    allowed, _ = check(result, cloud, policy_ok)
    if not allowed:
        return "public"
    if check(result, True)[0]:
        return "public"
    return "restricted" if "restricted" in labels(result) else "unknown"


def withheld(tool_name, reason, tell=None):
    """What a cloud model gets instead of a withheld result; `tell` replaces the general advice for the user."""
    return json.dumps({"ok": None, "withheld": True, "tool": tool_name,
                       "reason": f"The result was withheld from this cloud model because {reason}.",
                       "tell_the_user": tell or ("Say that the result is withheld from the cloud model and can be seen with the local "
                                                 "model (start a new chat with --provider local). Do not guess its content.")})
