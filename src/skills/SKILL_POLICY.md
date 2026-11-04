# AutoMC skill policy

How skills are loaded, and how they are added. Ported from AutoSAM's SKILL_POLICY.md and shortened.

## What is loaded

- **Always:** `index.md`, every bundled skill's `SKILL.md` (`core/`, `openmc-authoring/`, `openmc-validation/`, `openmc-execution/`,
  `openmc-post-processing/`, `general-tools/`, `reactors/triga/`) and `ERKB.yaml`. AutoMC is designed for a 128k-token context, so
  skills are not trimmed; a smaller context is a manual setting, not a supported target.
- **On demand:** every other file under `src/skills` (rule files, templates, the working examples), read with SkillLookupTool
  (`file`). A `SKILL.md` stays short and says which file holds what.
- **From facility packs:** each installed pack may provide a `skills/` folder (one subfolder per skill, each with a `SKILL.md`). A
  skill takes its pack's label. **A restricted pack's skills are loaded only when the model is local**; a cloud model never gets them.
- **Integrity:** `MANIFEST.json` lists the SHA-256 of every bundled file. A file that differs from the manifest is still loaded, but the
  chat says so at start-up; rebuild the manifest after an intended change (`python -m automc skills --build-manifest`).

## Writing a skill

- One folder, one `SKILL.md` with front matter `name` and `description`, then the guidance in plain sentences: when it applies, what to
  do, what never to do.
- Facts only from code, tests or a cited source; say what is unverified.
- **No restricted facility data in `src/skills/`** (no reference-model values, no digital-twin tables, no operating data); those belong
  in a restricted pack's skills. No personal names.
- An error-knowledge entry (`ERKB.yaml`) needs `detection_hint` (a substring of the log), `signature`, `background`, `suggestion`,
  `confidence` (high, medium, low) and `origin` (seen: met in this project; source: from the OpenMC source or documentation).
