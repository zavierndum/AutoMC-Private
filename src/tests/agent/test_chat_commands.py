"""
The chat's run controls (WD-0, devops/AutoMC_Workspace_Plan.md): /pause, /resume, /checkpoint, /stop and /cancel act on a job directly,
without the model, as the jobs panel's buttons will. /cancel kills the run, so it asks first; /resume of a stopped run is a restart,
which shows its plan and asks, as the command line does. The job service is replaced by fakes; nothing runs.
"""
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from tools.openmc import runs
from tools.openmc.errors import OpenMCError


def job(status="running", job_id="j1"):
    return {"job_id": job_id, "case": "c8", "status": status}


class RunControls(unittest.TestCase):
    def ui(self, answers=()):
        from agent.chat import ChatUI
        self.out = io.StringIO()
        answers = list(answers)
        self.asked = []

        def ask(q):
            self.asked.append(q)
            return answers.pop(0) if answers else ""
        return ChatUI(SimpleNamespace(), ask=ask, out=self.out)

    def test_pause_checkpoint_stop_and_resume_act_directly(self):
        for command in ("pause", "checkpoint", "stop", "resume"):
            with self.subTest(command=command), mock.patch.object(runs, "check", return_value=job("paused" if command == "resume" else "running")), \
                    mock.patch.object(runs, "control", return_value={**job(), "note": "takes effect at the end of the current batch"}) as control:
                ui = self.ui()
                self.assertTrue(ui.command(f"/{command} j1"))
                control.assert_called_once_with("j1", command)
                self.assertIn(f"{command} requested; takes effect", self.out.getvalue())
                self.assertEqual(self.asked, [])

    def test_cancel_asks_first(self):
        for answer, cancelled in (("", False), ("n", False), ("y", True)):
            with self.subTest(answer=answer), mock.patch.object(runs, "check", return_value=job()), \
                    mock.patch.object(runs, "cancel", return_value=job("cancelled")) as cancel:
                ui = self.ui([answer])
                ui.command("/cancel j1")
                self.assertIn("killed at once", self.asked[0])
                self.assertEqual(cancel.called, cancelled)
                self.assertIn("j1  cancelled" if cancelled else "not cancelled", self.out.getvalue())

    def test_resuming_a_stopped_run_is_a_restart_that_asks(self):
        plan = {"plan_id": "p", "text": "PLAN TEXT", "job_id": "j1"}
        for answer, started in (("", False), ("y", True)):
            with self.subTest(answer=answer), mock.patch.object(runs, "check", return_value=job("stopped")), \
                    mock.patch.object(runs, "plan_restart", return_value=plan), \
                    mock.patch.object(runs, "restart", return_value=job("running")) as restart, \
                    mock.patch.object(runs, "control") as control:
                ui = self.ui([answer])
                ui.command("/resume j1")
                self.assertIn("PLAN TEXT", self.out.getvalue())
                self.assertEqual(restart.called, started)
                if started:
                    restart.assert_called_once_with(plan, approved_by="chat")
                control.assert_not_called()

    def test_a_missing_id_and_a_refusal_are_said_plainly(self):
        ui = self.ui()
        ui.command("/pause")
        self.assertIn("/pause needs a job id", self.out.getvalue())
        with mock.patch.object(runs, "check", return_value=job("success")), \
                mock.patch.object(runs, "control", side_effect=OpenMCError("job_id", "job j1 is success; pause needs a running or paused job",
                                                                           kind="NotAllowed", exit_code=3)):
            ui.command("/pause j1")
        self.assertIn("pause needs a running or paused job", self.out.getvalue())


if __name__ == "__main__":
    unittest.main()
