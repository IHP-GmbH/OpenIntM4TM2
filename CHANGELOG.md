# Changelog

One row per intm4tm2 version, newest first; the newest row is the version in `pyproject.toml`. Earlier versions are described in their commit messages.

| Version | Change |
|---|---|
| 0.5.0 | Adds the routing variant `rdl2_sbump`, for dies on the SAC305 solder bump: the `rdl2` descriptor with the die landing on the solder-bump UBM opening (`ihp_sbump_anchor`, datatype 36 on Passiv, dfpad and Recog) instead of the pillar's. The bump was already an attach method here; it had no descriptor to route with. Adds the `SolderBumpPad` PCell, and `bump_mirror` places it for that anchor and checks it against the Padb rules instead of the Cu-pillar ones. Padb.c takes the 10 nm circle tolerance Padc.c has, so a circular solder-bump opening drawn at exactly 10 um enclosure passes. The PCell tech JSON gains the `sbump` purposes of Passiv, dfpad and Recog; no deck layer table or KiCad byte changes; layouts that carry the PDK version text change with it. |
| 0.4.1 | The two TM2.bR rules in the KiCad template's `.kicad_dru` now hold TopMetal2 copper to other TopMetal2 copper only. They also held a wide track or pad to the board outline's drawn stroke, which the deck has no rule for. Layouts that carry the PDK version text change with it. |
| 0.4.0 | Adds the technology manifest `libs.tech/adk/technology.json` and the routing descriptors it binds under `libs.tech/adk/routing/` (`base` and `rdl2`). They are the bytes interposer-pnr ships today, and from this version the copies here are the reference. No layer, rule or KiCad byte changes; layouts that carry the PDK version text change with it. |
