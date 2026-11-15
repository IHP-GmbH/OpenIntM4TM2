# OpenIntM4TM2

OpenIntM4TM2 is the KLayout PDK for the IHP 130 nm IntM4TM2 aluminium back-end interposer,
a passive carrier derived from SG13G2 that keeps only the Metal4 to TopMetal2 stack. It is
the substrate the IHP Open ADK reference flow assembles chiplets onto. It is a preview
release: DRC and LVS are still work in progress.

## Use

It runs in the ADK-Tools image. The container page,
https://ihp-open-adk-docs.readthedocs.io/en/latest/install/01_container.html, says how to get
the image and open a shell in it. In that shell:

```bash
/opt/adk-tools/venv/bin/python3 "$INTERPOSER_PDK_ROOT/libs.tech/klayout/tech/drc/run_drc.py" \
    --path <layout.gds> --topcell <cell>
```

This runs the interposer DRC on a layout; in the image this PDK is `$INTERPOSER_PDK_ROOT`.

## Documentation

The full documentation is at
https://ihp-open-adk-docs.readthedocs.io/en/latest/tools/05_openintm4tm2.html

## Release

Released as part of the IHP Open ADK. The release page lists the commit of every
repository in each release:
https://ihp-open-adk-docs.readthedocs.io/en/latest/install/00_release.html

## License

Apache-2.0. See `LICENSE`.
