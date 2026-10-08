# LPV project reference workflow

- Read `docs/SOURCE_REFERENCES.md` before consulting Unreal or RTXGI source.
- Prefer the local full-file copies under `references/source-cache/`; search them with `rg` and read only relevant ranges.
- Check `references/source-cache/manifest.json` for provenance, SHA-256 hashes, and availability. UE5 copies come from the local working tree; do not assume they are pristine upstream files.
- When an external source file is first obtained, cache its complete raw contents under its original repository-relative path and update the manifest. Do not repeatedly read the same file through the browser.
- Keep UE4.27 LPV and UE5 Lumen references distinct. Missing files are not cached references; never substitute reconstructed snippets or HTML pages for original source.
- `references/` is git-ignored local reference material. Keep it excluded from project commits.
