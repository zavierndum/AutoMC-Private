---
name: OpenMC execution
description: Starting, watching and controlling OpenMC runs with the job tools; statuses, start-up time, sequences, what to do when a run ends badly.
---

# openmc-execution

## When to use
The user asks to run, check, pause, resume, stop or cancel a simulation, or asks how a run is doing.

## Start
Asked to run a case by name, call OpenMCRunStartTool with that name at once: it says whether the case exists. Never decide yourself that
a name is missing or made up, and never use the job tools to find out whether a case exists (a case that never ran has no job).
1. Make sure the case is built (TRIGAInputBuilderTool for TRIGA cases).
2. Call OpenMCRunStartTool with the case (or cases, or a sweep). It returns the plan: histories, threads, an estimate, the run folder,
   the facility label, other active runs. It starts nothing.
3. The chat shows the plan to the user and asks. If the user says yes, the job starts and you get the job; if no, nothing runs.
4. Tell the user the job id and how long it may take, then end the turn.

Only one run at a time unless the plan says concurrent: if another run is active, say so and let the user choose.

## Check
OpenMCRunCheckTool returns the status, the phase, batches done and total, the latest k-eff and its error, the speed, the time left and
the last events. Report them in one or two sentences. phase "starting" means the run is loading its nuclear data: for the NETL TRIGA
model this takes about 100 s before batch 1, so a check right after the start shows no batches yet.

## Control
- pause: takes effect at the end of the current batch; the run writes a statepoint and waits, using no processor time.
- resume: a paused run continues at once. A stopped, interrupted or cancelled run continues from its statepoint only with the user's
  approval (OpenMCRunResumeTool returns a plan).
- stop: finishes the current batch, writes a statepoint and ends; the batches done give a result.
- cancel: kills the run at once; statepoints already written stay.

## Statuses
| status | meaning | what to do |
|---|---|---|
| running, paused | active | check again later |
| success | all batches done | read the result (RunResultReaderTool with the run folder) |
| stopped | ended early on request | read the result; continuing needs approval |
| failed | the run reported an error | read the error, its log tail and its known_errors, explain; do not restart blindly |
| interrupted | the process vanished without finishing (machine restart, killed) | it can continue from its last statepoint with approval |
| cancelled | killed on request | as interrupted |

## Sequences
A sweep or a list of cases runs as one job: cases already finished are skipped, the others run in turn; pause, resume and stop act on
the case that is running. The check lists every case with its status and k-eff.

## When it goes wrong
For a crash with no error line, lost particles, a geometry-debug run and its coverage, CAD file checks or a depletion that hangs, read
`troubleshooting.md` (SkillLookupTool, `file` set to `openmc-execution/rules/troubleshooting.md`).
