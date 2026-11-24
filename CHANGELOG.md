# Changelog

One row per intm4tm2 version, newest first; the newest row is the version in `pyproject.toml`. Earlier versions are described in their commit messages.

The version names what the PDK draws and checks with: the files that `libs.tech/klayout/intm4tm2_tests/test_version_content.py` selects. Adding, removing or changing one of those files, comments included, raises the version in `pyproject.toml`, at least PATCH:

- MINOR (0.x.0) when a design, or a tool that reads the PDK's files, has to change: a rule, layer, cell, parameter, deck, model, or the form of anything the PDK draws or writes is added, changed or removed.
- PATCH (0.x.y) otherwise.

A version rise touches five places, and the tests refuse a rise that misses one:

1. `version` in the `[project]` table of `pyproject.toml`;
2. a row in this file, newest first;
3. `revision` in `libs.tech/adk/technology.json`;
4. a row in `SHIPPED` in `libs.tech/klayout/intm4tm2_tests/test_kicad_rules.py`;
5. a row in `CONTENT` in `libs.tech/klayout/intm4tm2_tests/test_version_content.py`.

For 4 and 5, add the line the failing test prints. Rows of `SHIPPED` and `CONTENT` are only added, never edited or removed; review holds that. A change only to documentation, tests or CI does not raise the version.

The seal ring writes the version into every layout (`PDK version: intm4tm2 <version>`), so two checkouts at one version draw one label, and a layout's label names the content it was drawn with. A checkout with a tracked file changed writes `(local changes)` after the version.

| Version | Change |
|---|---|
| 0.8.0 | The seal ring's version label names the PDK and its version only: `PDK version: intm4tm2 0.8.0`, no longer the commit, so two commits of one version draw one layout byte for byte (timestamps aside). A checkout whose tracked files differ from its commit writes ` (local changes)` after the version; an install stamp `.shipped-ref` is no longer read. The version now follows the content of the files the PDK draws and checks with, as the rule above says, and `test_version_content.py` holds one digest of them per version from this one on. |
| 0.7.0 | The wide-metal space rules also hold design metal off the seal ring: `M4/M5.e` (0.24 um), `M4/M5.f` (0.60 um) and `TM2.bR` (5.0 um) now measure design metal against the ring's metal on the same layer, as they measure two design shapes, the wide seed taken from either side. Before, they skipped the ring, so a wide design plane at the minimum space (0.21 um on Metal4/Metal5) from the ring passed. The same three rules now also catch two wide lines closer than the rule, and a notch or slot narrower than the rule between two wide arms of one shape; they caught only a wide line beside a narrow one, as the sg13g2 deck they come from. The IHP reference layouts stay clean. No rule file or KiCad byte changes; layouts that carry the PDK version text change with it. |
| 0.6.0 | The DRC checks the seal-ring rules it left out. In the `sealring` deck, `Seal.b_<L>` holds drawn Metal4, Metal5, TopMetal1 and TopMetal2 at the layer's minimum space from the ring's metal and flags touching or overlap, `Seal.d.<V>` holds each via ring 1.30 um inside EdgeSeal, and `Seal.l1` keeps EdgeSeal 30 um inside the sealring boundary; in the `pad` deck, `Pad.d` keeps pad openings 7.50 um from EdgeSeal. The rule file (1.8) gains `Seal_d` and `Seal_l1`, which the seal-ring PCell now draws from, with unchanged geometry. A ring drawn by the PCell passes; design metal near or on the ring that passed before now fails. The KiCad `.kicad_dru` changes in its header only; layouts that carry the PDK version text change with it. |
| 0.5.0 | Adds the routing variant `rdl2_sbump`, for dies on the SAC305 solder bump: the `rdl2` descriptor with the die landing on the solder-bump UBM opening (`ihp_sbump_anchor`, datatype 36 on Passiv, dfpad and Recog) instead of the pillar's. The bump was already an attach method here; it had no descriptor to route with. Adds the `SolderBumpPad` PCell, and `bump_mirror` places it for that anchor and checks it against the Padb rules instead of the Cu-pillar ones. Padb.c takes the 10 nm circle tolerance Padc.c has, so a circular solder-bump opening drawn at exactly 10 um enclosure passes. The PCell tech JSON gains the `sbump` purposes of Passiv, dfpad and Recog; no deck layer table or KiCad byte changes; layouts that carry the PDK version text change with it. |
| 0.4.1 | The two TM2.bR rules in the KiCad template's `.kicad_dru` now hold TopMetal2 copper to other TopMetal2 copper only. They also held a wide track or pad to the board outline's drawn stroke, which the deck has no rule for. Layouts that carry the PDK version text change with it. |
| 0.4.0 | Adds the technology manifest `libs.tech/adk/technology.json` and the routing descriptors it binds under `libs.tech/adk/routing/` (`base` and `rdl2`). They are the bytes interposer-pnr ships today, and from this version the copies here are the reference. No layer, rule or KiCad byte changes; layouts that carry the PDK version text change with it. |
