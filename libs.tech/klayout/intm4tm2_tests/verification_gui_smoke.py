# SPDX-License-Identifier: Apache-2.0
"""Executed inside the real KLayout Qt application by test_verification_gui.py."""

import os
from pathlib import Path
import sys
import time
from dataclasses import replace

import pya

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ["INTERPOSER_TEST_OUTPUT"])
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "python"))
import interposer_verification_gui as gui


def wait(dialog):
    deadline = time.monotonic() + 60
    while dialog.proc is not None and time.monotonic() < deadline:
        pya.Application.instance().process_events()
        time.sleep(0.01)
    assert dialog.proc is None, "GUI job timed out: " + dialog.status.text
    print(dialog.status.text, flush=True)
    print(dialog.logs.toPlainText(), flush=True)


def open_layout(path, top):
    cv = pya.Application.instance().main_window().load_layout(str(path), "intm4tm2", 1)
    cv.cell = cv.layout().cell(top)
    return cv


def macro_dialog(kind):
    menu = pya.Application.instance().main_window().menu()
    deadline = time.monotonic() + 3
    while not menu.items("intm4tm2_menu.verification") and time.monotonic() < deadline:
        pya.Application.instance().process_events()
        time.sleep(0.01)
    print("Verification menu:", menu.items("intm4tm2_menu.verification"), flush=True)
    actions = [menu.action(path) for path in menu.items("intm4tm2_menu.verification")]
    selected = [action for action in actions if action.title == "Interposer " + kind.upper() + "…"]
    assert len(selected) == 1, "Missing/duplicate verification menu: " + str([a.title for a in actions])
    if not (OUT / "verification-menu.png").exists():
        # Capture the actual Qt menu populated from the shipped .lym metadata.
        bar = pya.Application.instance().main_window().menuBar
        topmenus = [a.menu for a in bar.actions() if a.text.replace("&", "") == "IntM4TM2 Interposer"]
        submenus = [a.menu for a in topmenus[0].actions() if a.text.replace("&", "") == "Verification"]
        assert len(submenus) == 1
        submenus[0].popup(pya.QPoint(0, 0))
        pya.Application.instance().process_events()
        assert submenus[0].grab().save(str(OUT / "verification-menu.png"))
        submenus[0].hide()
    selected[0].trigger()
    dialog = gui._dialogs[-1]
    options = dialog.options()
    options.update(python=os.environ["INTERPOSER_TEST_PYTHON"], output_root=str(OUT))
    dialog.set_options(options)
    return dialog


def run_smoke():
    print("Starting real-menu GUI smoke", flush=True)
    try:
        tech = pya.Technology.create_technology("intm4tm2")
        tech.load(str(ROOT / "tech" / "intm4tm2.lyt"))
        drc = macro_dialog("drc")
        assert "DENSITY EXCLUDED" in drc.scope.text
        options = drc.options()
        options.update(selection="topmetal2", topcell="topmetal2_clean", run_mode="flat", threads=1)
        drc.set_options(options)
        drc.save_options()
        restored = gui.VerificationDialog("drc")
        assert restored.options() == options
        restored.close()
        fixture = ROOT / "tech/drc/testing/testcases/unit/topmetal2.gds"
        origin = open_layout(fixture, "topmetal2_clean")
        drc.start_run()
        assert drc.proc is not None, drc.status.text
        drc.close()
        assert drc.proc is not None, "Closing a dialog must not abandon a running job"
        wait(drc)
        assert drc.status.text.startswith("PASS within scope:"), drc.status.text
        drc.open_report()
        assert "Report not attached:" not in drc.logs.toPlainText()
        assert origin.view().num_rdbs() == 1
        first_dir = drc.job.run_dir
        drc.start_run()
        wait(drc)
        assert drc.job.run_dir != first_dir and first_dir.is_dir()
        # Open a different tab. The old report must stay bound to the original view.
        other = open_layout(fixture, "topmetal2_viol")
        drc.open_report()
        assert origin.view().num_rdbs() == 2 and other.view().num_rdbs() == 0
        options["topcell"] = "topmetal2_viol"
        drc.set_options(options)
        drc.start_run()
        wait(drc)
        assert drc.status.text.startswith("NOT PASSED"), drc.status.text
        drc.grab().save(str(OUT / "drc-dialog.png"))
        other.technology = ""
        drc.open_report()
        assert "Report not attached:" in drc.logs.toPlainText()
        drc.close()

        density = macro_dialog("drc")
        density_options = density.options()
        density_options.update(selection="density", density_sanity=True, run_mode="flat", threads=1)
        for top, verdict in (("density_sanity_clean", "PASS within scope:"),
                             ("density_sanity_viol", "NOT PASSED")):
            density_options["topcell"] = top
            density.set_options(density_options)
            density_view = open_layout(ROOT / "tech/drc/testing/testcases/unit/density.gds", top)
            density.start_run()
            assert density.proc is not None, density.status.text
            wait(density)
            assert density.status.text.startswith(verdict), density.status.text
            assert "Density only, with boundary sanity" in density.scope.text
            density.open_report()
            assert density_view.view().num_rdbs() == 1
            if top.endswith("viol"):
                for rule in ("DEN.BND.1", "DEN.BND.2", "DEN.BND.3"):
                    assert rule in density.logs.toPlainText(), density.logs.toPlainText()
        assert density.grab().save(str(OUT / "density-dialog.png"))
        density.close()

        lvs_dir = ROOT / "tech/lvs/testing/testcases/unit"
        open_layout(lvs_dir / "lvs_clean.gds", "LVS_CLEAN")
        lvs = macro_dialog("lvs")
        lvs.start_run()
        assert lvs.proc is None and "requires a SPICE" in lvs.status.text
        options = lvs.options()
        options.update(reference=str(lvs_dir / "lvs_clean_reference.cir"))
        lvs.set_options(options)
        lvs.start_run()
        assert lvs.proc is not None, lvs.status.text
        wait(lvs)
        assert lvs.status.text.startswith("PASS within scope:"), lvs.status.text
        lvs.open_report()
        assert "Report not attached:" not in lvs.logs.toPlainText()
        options.update(mode="connectivity", reference="")
        lvs.set_options(options)
        lvs.start_run()
        wait(lvs)
        assert "PASS within scope:" in lvs.status.text and "ONLY" in lvs.status.text
        lvs.open_report()
        assert "no comparison database" in lvs.logs.toPlainText()
        lvs.grab().save(str(OUT / "lvs-dialog.png"))
        # Cancel must also stop a descendant which outlives its Python supervisor.
        real_prepare = gui.prepare_job
        child_code = ("import signal,time,os; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                      "print('CHILD_READY='+str(os.getpid()),flush=True); time.sleep(60)")
        supervisor_code = ("import subprocess,sys,time; "
                           "subprocess.Popen([sys.executable,'-u','-c'," + repr(child_code) + "]); "
                           "time.sleep(60)")
        def prepare_cancel(*args, **kwargs):
            job = real_prepare(*args, **kwargs)
            return replace(job, command=(os.environ["INTERPOSER_TEST_PYTHON"], "-u", "-c", supervisor_code))
        gui.prepare_job = prepare_cancel
        lvs.start_run()
        deadline = time.monotonic() + 5
        while "\nCHILD_READY=" not in lvs.logs.toPlainText() and time.monotonic() < deadline:
            pya.Application.instance().process_events()
            time.sleep(0.01)
        child_pid = int(lvs.logs.toPlainText().split("\nCHILD_READY=")[-1].splitlines()[0])
        lvs.cancel_run()
        wait(lvs)
        assert lvs.status.text.startswith("CANCELLED"), lvs.status.text
        assert not lvs.report_button.enabled
        child_status = Path("/proc") / str(child_pid) / "stat"
        deadline = time.monotonic() + 3
        while child_status.exists() and child_status.read_text().split(") ", 1)[1][0] != "Z":
            assert time.monotonic() < deadline, "Cancelled descendant is still running"
            time.sleep(0.01)
        gui.prepare_job = real_prepare
        # GUI-state guard, not only a mocked pure-helper dirty flag.
        cv = pya.CellView.active()
        cv.cell.shapes(cv.layout().layer(134, 0)).insert(pya.Box(0, 0, 100, 100))
        lvs.start_run()
        assert lvs.proc is None and "Save" in lvs.status.text
        lvs.close()
        print("INTERPOSER_GUI_SMOKE_PASS", flush=True)
    except BaseException:
        import traceback
        traceback.print_exc()
        # Avoid an interactive save/exception prompt in this disposable GUI.
        # Never terminate a user application: this script is a test entry only.
        for dialog in gui._dialogs:
            if dialog.proc is not None:
                try:
                    os.killpg(dialog.proc.pid, 9)
                except ProcessLookupError:
                    pass
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    else:
        # The dirty-layout guard deliberately left an unsaved test edit.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


run_smoke()
