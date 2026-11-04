"""
AutoMC command line. Run from AutoMC/src, in the WSL automc environment:

    python -m automc triga <command> [options]    the TRIGA Input Builder: build, sweep, preview, spec, presets, assumptions, read-sweep
    python -m automc read-run RUN_FOLDER          summarize a finished OpenMC run; read-run --compare A B checks that two runs agree
    python -m automc run start CASE | check | watch | pause | resume | checkpoint | stop | cancel | jobs
                                                  runs as managed jobs; a start asks you first (python -m automc run -h)
    python -m automc docs ingest FOLDER | list | check | watch | search WORDS | cancel
                                                  knowledge bases from folders of PDFs and images; a run asks you first (docs -h)
    python -m automc chat [--provider NAME]       talk to the agent (config/llm.yaml chooses the model)
    python -m automc models list | check NAME     the language models: which are ready; does one answer (--tools: and call a tool)
    python -m automc prompt [--show]              what the model is told: system prompt, skills, facility packs
    python -m automc eval list | run [--case ID]  score the agent on the written cases (evals/cases.yml) with a local model
    python -m automc skills --build-manifest | --verify    the bundled skills' checksums (skills/MANIFEST.json)
    python -m automc data list | fetch | verify [EXAMPLE ...]    the input files some working examples need (CAD, meshes, ENDF),
                                                  downloaded into the data cache and checked against their SHA-256
    python -m automc tool --list                  the agent's tools, with what each does and its limits
    python -m automc tool NAME '{"key": value}'   call one tool exactly as the agent does (JSON arguments in, JSON result out)
    python -m automc tool NAME --schema           the tool's arguments
    python -m automc --version                    AutoMC's version and the fingerprint of its prompt, skills and evaluation cases
    python -m automc workspace upgrade [--dry-run] | usage    move an older workspace's hidden items into .automc/; what takes space
    python -m automc config show [--json]         every setting in effect (folders, labels, links, mode) and where it came from

--workspace DIR (before or after the command) works in DIR instead of AutoMC/workspace (or AUTOMC_WORKSPACE).
Add -h to any command for its options and examples.
"""
from __future__ import annotations

import json
import sys

# The commands that use what AutoMC keeps in the workspace's .automc/ folder (runs, knowledge bases, chats, model checks): they refuse
# an older layout until `workspace upgrade` has moved it. Building, previewing and reading runs need only the cases.
STATEFUL = ("chat", "run", "docs", "eval", "tool", "models")


def _tool(argv):
    from tools import registry
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    if argv[0] == "--list":
        for t in registry.describe():
            print(f"{t['name']}\n    {t['description']}\n")
        return 0
    name, rest = argv[0], argv[1:]
    if rest == ["--schema"]:
        match = [t for t in registry.describe() if t["name"] == name]
        if not match:
            print(f"no tool {name!r}; tools: {registry.names()}", file=sys.stderr)
            return 2
        print(json.dumps(match[0]["parameters"], indent=2))
        return 0
    if len(rest) > 1:
        print("give the arguments as one JSON object, in quotes", file=sys.stderr)
        return 2
    try:
        args = json.loads(rest[0]) if rest else {}
    except json.JSONDecodeError as e:
        print(f"the arguments are not valid JSON: {e}", file=sys.stderr)
        return 2
    out = registry.call(name, args)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else int(out.get("error", {}).get("exit_code") or 1)


def _prompt(argv):
    import argparse
    from agent import llm, prompt
    ap = argparse.ArgumentParser(prog="python -m automc prompt")
    ap.add_argument("--provider")
    ap.add_argument("--show", action="store_true", help="print the whole system prompt")
    a = ap.parse_args(argv)
    from agent import discovery
    p = discovery.resolve(a.provider, require_ready=False, require_checked=False) if a.provider else llm.provider(None, require_ready=False)
    text, info = prompt.system_prompt(p)
    if a.show:
        print(text)
    print(f"system prompt of AutoMC {info['version']} (prompt and skills {info['fingerprint']}) for {p.describe()}: {len(text):,} "
          f"characters (about {len(text) // 4:,} tokens)")
    print(f"skills: {', '.join(info['skills'])}")
    if info.get("pack_skills_on_demand"):
        print(f"facility-pack skills, read on demand: {', '.join(info['pack_skills_on_demand'])}")
    if info["skipped_packs"]:
        print(f"left out for a cloud model: skills of {', '.join(info['skipped_packs'])}")
    print("manifest: " + ("matches" if not info["manifest"] else ", ".join(f"{f} {w}" for f, w in info["manifest"])))
    return 0


def _skills(argv):
    from agent import prompt
    if argv == ["--build-manifest"]:
        data = prompt.build_manifest()
        print(f"MANIFEST.json written: {data['file_count']} files")
        return 0
    if argv == ["--verify"]:
        diff = prompt.check_manifest()
        print("skills match MANIFEST.json" if not diff else "; ".join(f"{f}: {w}" for f, w in diff))
        return 0 if not diff else 1
    print("python -m automc skills --build-manifest | --verify")
    return 2


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--workspace" in argv:                                     # anywhere: before or after the command
        i = argv.index("--workspace")
        if i + 1 >= len(argv):
            print("--workspace needs a folder", file=sys.stderr)
            return 2
        import automc_paths
        automc_paths.use_workspace(argv[i + 1])                    # before any other AutoMC module is imported
        del argv[i:i + 2]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    if argv[0] == "--version":
        import automc_version
        print(automc_version.describe(include_cases=True))
        return 0
    command, rest = argv[0], argv[1:]
    if command == "workspace":
        import automc_workspace
        return automc_workspace.main(rest)
    if command == "config":
        import automc_workspace
        return automc_workspace.config_main(rest)
    if command in STATEFUL:
        import automc_workspace
        try:
            automc_workspace.check()                              # an older layout: say how to upgrade, touch nothing
        except automc_workspace.LayoutError as e:
            print(f"{e}", file=sys.stderr)
            return 3
        automc_workspace.ensure_format()
    if command == "triga":
        from tools.reactors.triga.__main__ import main as triga_main
        return triga_main(rest)
    if command == "read-run":
        from tools.openmc.results import main as read_run_main
        return read_run_main(rest)
    if command == "tool":
        return _tool(rest)
    if command == "chat":
        from agent.chat import main as chat_main
        return chat_main(rest)
    if command == "models":
        from agent.models import main as models_main
        return models_main(rest)
    if command == "eval":
        from agent.evals import main as eval_main
        return eval_main(rest)
    if command == "prompt":
        return _prompt(rest)
    if command == "skills":
        return _skills(rest)
    if command == "data":
        from tools.openmc.example_data import main as data_main
        return data_main(rest)
    if command == "run":
        from tools.openmc.run_cli import main as run_main
        return run_main(rest)
    if command == "docs":
        from tools.documents.cli import main as docs_main
        return docs_main(rest)
    print(f"unknown command {command!r}\n\n{__doc__.strip()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
