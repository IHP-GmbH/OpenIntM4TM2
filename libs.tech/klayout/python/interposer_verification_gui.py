# SPDX-License-Identifier: Apache-2.0
"""KLayout dialogs for the PDK runners, not an assembly orchestration layer."""

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import time

import pya

from interposer_verification import (SELECTIONS, defaults, prepare_job, result_description,
                          scope_description, validate_options)

_dialogs = []  # Keep Python callbacks alive while the modeless dialogs are open.


class VerificationDialog(pya.QDialog):
    def __init__(self, kind):
        super().__init__(pya.Application.instance().main_window())
        self.kind = kind
        self.key = "intm4tm2-verification-" + kind
        self.proc = None
        self.job = None
        self.cancelled = False
        self.kill_at = None
        self.origin = None
        self.output = None
        self.reader = None
        self.windowTitle = "IntM4TM2 interposer " + kind.upper()
        self.resize(820, 650)
        outer = pya.QVBoxLayout(self)
        notice = pya.QLabel(
            "Checks the saved interposer layout only, not a combined assembly.\n"
            "No chiplet-internal or assembly DRC/LVS is performed here.")
        notice.wordWrap = True
        outer.addWidget(notice)
        self.form_widget = pya.QWidget()
        self.form = pya.QFormLayout(self.form_widget)
        outer.addWidget(self.form_widget)
        self.fields = {}
        self._line("topcell", "Top cell (blank = selected cell)")
        self._line("output_root", "Results parent (blank = beside layout)", directory=True)
        self._combo("run_mode", "Run mode", ["tiling", "flat", "deep"] if kind == "drc" else ["flat", "deep"])
        if kind == "drc":
            self._combo("selection", "Rules", list(SELECTIONS.values()))
            threads = pya.QSpinBox()
            threads.minimum = 1
            threads.maximum = max(1, os.cpu_count() or 1)
            self.fields["threads"] = threads
            self.form.addRow("Threads (one deck at a time)", threads)
            self._check("density_sanity", "Boundary sanity for density-only runs")
            self.fields["density_sanity"].toggled = self._update_scope
            self.fields["selection"].currentIndexChanged = self._update_scope
        else:
            self._combo("mode", "Check", ["Reference SPICE comparison", "Labeled connectivity only"])
            self._line("reference", "Reference SPICE", file=True)
            self._check("combine_devices", "Combine parallel MIM capacitors")
            detail = pya.QLabel("Extraction: M4–TM2 connectivity and MIM capacitors.\n"
                "Filler /22 and slits /24 are not electrically modeled; resistor/inductor extraction is not covered.")
            detail.wordWrap = True
            outer.addWidget(detail)
            self.fields["mode"].currentIndexChanged = self._update_scope
        self._line("python", "Python executable (must import klayout.db)")
        self.scope = pya.QLabel()
        self.scope.wordWrap = True
        outer.addWidget(self.scope)
        self.status = pya.QLabel("Not run")
        self.status.wordWrap = True
        outer.addWidget(self.status)
        self.logs = pya.QPlainTextEdit()
        self.logs.readOnly = True
        outer.addWidget(self.logs)
        buttons = pya.QHBoxLayout()
        outer.addLayout(buttons)
        self.run_button = pya.QPushButton("Run")
        self.run_button.clicked = self.start_run
        self.cancel_button = pya.QPushButton("Cancel run")
        self.cancel_button.enabled = False
        self.cancel_button.clicked = self.cancel_run
        self.report_button = pya.QPushButton("Open report")
        self.report_button.enabled = False
        self.report_button.clicked = self.open_report
        self.save_button = pya.QPushButton("Save options")
        self.save_button.clicked = self.save_options
        self.reset_button = pya.QPushButton("Reset options")
        self.reset_button.clicked = lambda: self.set_options(defaults(self.kind))
        close = pya.QPushButton("Close")
        close.clicked = self.close
        for button in (self.run_button, self.cancel_button, self.report_button,
                       self.save_button, self.reset_button, close):
            buttons.addWidget(button)
        self.timer = pya.QTimer(self)
        self.timer.interval = 100
        self.timer.timeout = self.poll_run
        self.set_options(defaults(kind))
        raw = pya.Application.instance().get_config(self.key)
        if raw:
            try:
                self.set_options(validate_options(kind, json.loads(raw)))
            except (ValueError, TypeError) as exc:
                self.status.text = "Saved settings not loaded: " + str(exc)

    def _line(self, key, title, directory=False, file=False):
        field = pya.QLineEdit()
        self.fields[key] = field
        if directory or file:
            row = pya.QHBoxLayout()
            row.addWidget(field)
            browse = pya.QPushButton("Browse…")
            def choose():
                path = (pya.QFileDialog.getExistingDirectory(self, title, field.text)
                        if directory else pya.QFileDialog.getOpenFileName(self, title, field.text,
                            "SPICE netlists (*.cir *.spice *.spi *.cdl);;All files (*)"))
                if path:
                    field.text = path
            browse.clicked = choose
            row.addWidget(browse)
            self.form.addRow(title, row)
        else:
            self.form.addRow(title, field)

    def _combo(self, key, title, items):
        field = pya.QComboBox()
        for item in items:
            field.addItem(item)
        self.fields[key] = field
        self.form.addRow(title, field)

    def _check(self, key, title):
        field = pya.QCheckBox(title)
        self.fields[key] = field
        self.form.addRow(field)

    def options(self):
        options = {}
        for key, default in defaults(self.kind).items():
            field = self.fields[key]
            if key == "selection":
                value = list(SELECTIONS)[field.currentIndex]
            elif key == "mode":
                value = ("reference", "connectivity")[field.currentIndex]
            elif key == "run_mode":
                value = field.currentText
            elif isinstance(default, bool):
                value = field.checked
            elif isinstance(default, int):
                value = field.value
            else:
                value = field.text
            options[key] = value
        return validate_options(self.kind, options)

    def set_options(self, options):
        for key, value in options.items():
            field = self.fields[key]
            if key == "selection":
                field.currentIndex = list(SELECTIONS).index(value)
            elif key == "mode":
                field.currentIndex = ("reference", "connectivity").index(value)
            elif key == "run_mode":
                field.currentIndex = field.findText(value)
            elif isinstance(value, bool):
                field.checked = value
            elif isinstance(value, int):
                field.value = value
            else:
                field.text = value
        self._update_scope()

    def _update_scope(self, *unused):
        # Construction can emit a selection signal before the status label exists.
        if not hasattr(self, "scope"):
            return
        options = self.options()
        self.scope.text = scope_description(self.kind, options)
        if self.kind == "drc":
            self.fields["density_sanity"].enabled = options["selection"] == "density"
        else:
            self.fields["reference"].enabled = options["mode"] == "reference"

    def save_options(self):
        pya.Application.instance().set_config(self.key, json.dumps(self.options(), sort_keys=True))

    def start_run(self):
        if self.proc is not None:
            return
        try:
            if os.name != "posix":
                raise ValueError("These cancellable runner dialogs currently require a Linux/POSIX host")
            cv = pya.CellView.active()
            if cv is None or not cv.is_valid() or cv.cell is None:
                raise ValueError("Open an interposer layout and select its top cell")
            self.job = prepare_job(self.kind, self.options(), cv.filename(), cv.cell.name,
                cv.technology, cv.is_dirty(), [cell.name for cell in cv.layout().each_cell()])
            self.origin = cv
            self.cancelled = False
            self.kill_at = None
            self.logs.clear()
            heading = (self.job.scope + "\nLayout: " + str(self.job.layout) +
                "\nTop: " + self.job.topcell + "\nInput SHA256: " + self.job.layout_digest +
                "\nReference SHA256: " + str(self.job.reference_digest) +
                "\nResults: " + str(self.job.run_dir) + "\n" + shlex.join(self.job.command))
            self.save_options()
            self.output = open(self.job.run_dir / "run.log", "wb")
            self.output.write((heading + "\n").encode("utf-8"))
            self.output.flush()
            self.reader = open(self.job.run_dir / "run.log", "r", errors="replace")
            self.form_widget.enabled = False
            self.save_button.enabled = False
            self.reset_button.enabled = False
            self.proc = subprocess.Popen(self.job.command, stdout=self.output,
                stderr=subprocess.STDOUT, start_new_session=True)
            self.run_button.enabled = False
            self.report_button.enabled = False
            self.cancel_button.enabled = True
            self.status.text = "Running: " + self.job.scope
            self.timer.start()
        except Exception as exc:
            self.status.text = "Not run: " + str(exc)
            if self.proc is None:
                self._close_logs()
                self.form_widget.enabled = True
                self.save_button.enabled = True
                self.reset_button.enabled = True

    def cancel_run(self):
        if self.proc is not None and self.proc.poll() is None:
            self.cancelled = True
            self.kill_at = time.monotonic() + 2
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            self.cancel_button.enabled = False
            self.status.text = "Cancelling this verification job…"

    def poll_run(self):
        if self.proc is None:
            return
        output = self.reader.read(65536)
        if output:
            self.logs.appendPlainText(output.rstrip())
        code = self.proc.poll()
        # A terminated Python supervisor can leave KLayout descendants alive.
        # Keep the owned process-group identity until the cancellation grace
        # has elapsed, even if the supervisor already exited and was reaped.
        if self.kill_at is not None:
            if time.monotonic() < self.kill_at:
                return
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.kill_at = None
        if code is None:
            return
        self.logs.appendPlainText(self.reader.read().rstrip())
        self.timer.stop()
        self.proc = None
        self.status.text = result_description(self.job, code, self.cancelled)
        self.output.write(("\n" + self.status.text + "\n").encode("utf-8"))
        self._close_logs()
        self.logs.appendPlainText(self.status.text)
        self.form_widget.enabled = True
        self.save_button.enabled = True
        self.reset_button.enabled = True
        self.run_button.enabled = True
        self.cancel_button.enabled = False
        self.report_button.enabled = not self.cancelled and code in (0, 1)

    def _close_logs(self):
        for stream in (self.reader, self.output):
            if stream is not None:
                stream.close()
        self.reader = self.output = None

    def open_report(self):
        try:
            cv = self.origin
            if (cv is None or not cv.is_valid() or cv.is_dirty() or
                    Path(cv.filename()).resolve() != self.job.layout or cv.technology != "intm4tm2" or
                    cv.layout().cell(self.job.topcell) is None or
                    not self.job.inputs_unchanged()):
                raise ValueError("Original saved layout/top changed or closed. Results remain in " + str(self.job.run_dir))
            view = cv.view()
            if self.kind == "drc":
                # The CLI produces one merged report when multiple decks run.
                merged = list(self.job.run_dir.glob("*_full.lyrdb"))
                reports = merged or list(self.job.run_dir.glob("*.lyrdb"))
                if not reports:
                    raise ValueError("No DRC report produced; inspect run.log")
                for report in reports:
                    rdb = pya.ReportDatabase()
                    rdb.load(str(report))
                    view.show_rdb(view.add_rdb(rdb), cv.index())
            elif self.job.reference is None:
                self.logs.appendPlainText("Connectivity-only run: extracted .cir and run.log in " + str(self.job.run_dir) + "; no comparison database.")
            else:
                report = self.job.run_dir / (self.job.layout.stem + ".lvsdb")
                database = pya.LayoutVsSchematic()
                database.read(str(report))
                view.show_lvsdb(view.add_lvsdb(database), cv.index())
        except Exception as exc:
            self.logs.appendPlainText("Report not attached: " + str(exc))

    def closeEvent(self, event):
        if self.proc is not None:
            self.status.text = "Run active: cancel it or wait before closing this dialog"
            event.ignore()
        else:
            event.accept()
            if self in _dialogs:
                _dialogs.remove(self)

    def reject(self):
        self.close()  # Escape must honor the same active-job guard.


def show(kind):
    dialog = VerificationDialog(kind)
    _dialogs.append(dialog)
    dialog.show()
    return dialog
