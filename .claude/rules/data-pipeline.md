# Rules: Data Pipeline

Applied when working on `data/`, `scripts/data/`, conversion scripts, or anything touching geospatial data.

- `data/interim/tile_index.json` is critical — never delete, never overwrite without reading first. It maps tile filenames to CRS, affine transforms, and geographic bounds.
- All YOLO labels are polygon segmentation format: `0 x1 y1 x2 y2 ... xn yn` (normalized 0-1, class always 0).
- CRS and affine transforms must survive every transformation step. If a script doesn't preserve them, fix it before proceeding.
- New datasets (e.g., Duke/Figshare) must produce a `tile_index.json` equivalent for their tiles before labels are merged.
- When tiling new imagery, chip size is 640×640 pixels. Overlap is configurable but default is no overlap.
- Do not mix Duke (0.3m GSD) and NAIP (~0.6m GSD) tiles in the same `data.yaml` without documenting the resolution difference.
- Roboflow metadata is stored in `data/interim/roboflow_metadata.json` — preserve this for traceability.
- Never modify files under `data/yolo/naip/` directly; generate new versions or use a separate `data/yolo/duke/` path.
