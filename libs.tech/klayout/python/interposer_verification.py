# SPDX-License-Identifier: Apache-2.0
"""Small, GUI-independent adapter for the existing interposer verification CLIs."""
# Annotations stay unevaluated, so the menus load under an embedded Python older than 3.10.
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import MappingProxyType


TECH = Path(__file__).resolve().parents[1] / "tech"
_spec = importlib.util.spec_from_file_location(
    "_interposer_verification_decks", TECH / "drc" / "intm4tm2_decks.py")
_registry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_registry)
DECKS = _registry.AVAILABLE_DECKS
# The DRC rule selections the dialog offers, in its order, with their labels.
# Read-only, as the registry's relocation map is, since the dialog shares it.
SELECTIONS = MappingProxyType({"default": "Default physical rules, density excluded",
                               "full": "Full interposer, density and boundary sanity",
                               **{deck: deck for deck in DECKS}})


def defaults(kind):
    if kind not in ("drc", "lvs"):
        raise ValueError("Verification must be drc or lvs")
    common = {"python": "python3", "output_root": "", "topcell": ""}
    if kind == "drc":
        return dict(common, run_mode="tiling", threads=min(2, max(1, os.cpu_count() or 1)), selection="default",
                    density_sanity=True)
    return dict(common, run_mode="deep", reference="", mode="reference",
                combine_devices=False)


def validate_options(kind, saved):
    """Reject bad persisted settings instead of silently broadening/reducing scope."""
    options = defaults(kind)
    if not isinstance(saved, dict) or set(saved) - set(options):
        raise ValueError("Invalid saved verification options; reset the dialog settings")
    options.update(saved)
    for key, value in options.items():
        if type(value) is not type(defaults(kind)[key]):
            raise ValueError("Invalid option type: " + key)
    modes = ("flat", "deep", "tiling") if kind == "drc" else ("flat", "deep")
    if options["run_mode"] not in modes:
        raise ValueError("Unsupported run mode")
    if kind == "drc":
        if options["selection"] not in SELECTIONS:
            raise ValueError("Select a registered interposer DRC deck")
        if not 1 <= options["threads"] <= max(1, os.cpu_count() or 1):
            raise ValueError("Threads must be between 1 and the host CPU count")
    elif options["mode"] not in ("reference", "connectivity"):
        raise ValueError("Choose reference comparison or connectivity checks")
    return options


# The dialog never passes --require_sealring, so the SEAL.REQ rules stay off. A
# layout without EdgeSeal (39/0) passes the rules that measure to it (Seal.n,
# Pas.c, Pad.dR, Padc.d, Padb.d, LBE.d), one without the 39/4 boundary skips
# Seal.l, and an open ring or shapes outside it go uncaught. Every DRC scope
# says so.
NO_SEAL_REQ = ("seal-ring requirement (SEAL.REQ) not checked, so a missing or "
               "open ring, a missing 39/4 boundary (Seal.l then skipped), or "
               "shapes outside the ring can pass")


def scope_description(kind, options):
    if kind == "lvs":
        return ("Interposer LVS against reference SPICE (metal stack + MIM)"
                if options["mode"] == "reference" else
                "Interposer labeled connectivity ONLY; no schematic comparison")
    selection = options["selection"]
    if selection == "default":
        scope = "Interposer default physical decks; DENSITY EXCLUDED"
    elif selection == "full":
        scope = "All interposer decks, including density and boundary sanity"
    elif selection == "density":
        sanity = "with" if options["density_sanity"] else "without"
        scope = "Density only, " + sanity + " boundary sanity; other DRC not run"
    else:
        scope = "Selected interposer deck: " + selection + "; density and other decks not run"
    return scope + "; " + NO_SEAL_REQ


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class Job:
    kind: str
    command: tuple
    run_dir: Path
    layout: Path
    topcell: str
    layout_digest: str
    scope: str
    reference: Path | None = None
    reference_digest: str | None = None

    def inputs_unchanged(self):
        try:
            return (file_digest(self.layout) == self.layout_digest and
                    (self.reference is None or
                     file_digest(self.reference) == self.reference_digest))
        except OSError:
            return False


def prepare_job(kind, saved, filename, active_top, technology, dirty, cell_names):
    """Bind saved input bytes; allocate a new child directory, never reuse/delete."""
    options = validate_options(kind, saved)
    if technology != "intm4tm2":
        raise ValueError("Select the intm4tm2 technology on an interposer-only layout")
    if dirty or not filename:
        raise ValueError("Save the layout with Save / Save As before verification")
    layout = Path(filename).expanduser().resolve()
    if not layout.is_file():
        raise ValueError("Saved layout file does not exist: " + str(layout))
    topcell = options["topcell"].strip() or active_top
    if not topcell or topcell not in cell_names:
        raise ValueError("Top cell is not in the active layout: " + str(topcell))
    python = shutil.which(options["python"])
    if not python:
        raise ValueError("Python executable not found: " + options["python"])
    reference = None
    if kind == "lvs" and options["mode"] == "reference":
        if not options["reference"].strip():
            raise ValueError("Reference comparison requires a SPICE netlist")
        reference = Path(options["reference"]).expanduser().resolve()
        if not reference.is_file():
            raise ValueError("Reference SPICE netlist does not exist: " + str(reference))
    layout_digest = file_digest(layout)
    reference_digest = file_digest(reference) if reference else None
    parent = (Path(options["output_root"]).expanduser().resolve()
              if options["output_root"].strip() else layout.parent / "verification_runs")
    parent.mkdir(parents=True, exist_ok=True)
    safe_top = re.sub(r"[^a-zA-Z0-9_.-]", "_", topcell)[:60]
    run_dir = Path(tempfile.mkdtemp(prefix=f"{kind}_{safe_top}_", dir=parent))
    command = [python, "-u", str(TECH / kind / f"run_{kind}.py"),
               "--path" if kind == "drc" else "--layout", str(layout),
               "--topcell", topcell, "--run_dir", str(run_dir),
               "--run_mode", options["run_mode"]]
    if kind == "drc":
        # One KLayout process at a time. A crashed deck exits 2 (ERROR) in either mode.
        command += ["--mp", "1", "--threads", str(options["threads"])]
        selection = options["selection"]
        if selection == "full":
            command += ["--density", "--density_sanity"]
        elif selection != "default":
            command += ["--deck", selection]
            if selection == "density" and options["density_sanity"]:
                command += ["--density_sanity"]
    else:
        if reference:
            command += ["--netlist", str(reference)]
        if options["combine_devices"]:
            command += ["--combine_devices"]
    return Job(kind, tuple(command), run_dir, layout, topcell, layout_digest,
               scope_description(kind, options) + "; on cell " + topcell, reference,
               reference_digest)


def result_description(job, returncode, cancelled=False):
    if cancelled:
        return "CANCELLED: no verification verdict"
    if not job.inputs_unchanged():
        return "INCONCLUSIVE: an input changed during the run; do not reuse this result"
    if returncode == 0:
        return "PASS within scope: " + job.scope + ". Not assembly or fabrication signoff."
    if returncode == 1:
        return "NOT PASSED: inspect findings and run log. Scope: " + job.scope
    return "ERROR / INCONCLUSIVE: no passing verdict; inspect run.log"
