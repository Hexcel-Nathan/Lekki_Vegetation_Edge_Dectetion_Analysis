# Lekki Coastal Vulnerability Index — Vegetation Edge & Waterline Change Pipeline

Satellite-derived vegetation-edge and waterline change analysis for a ~69 km stretch of the Lekki coastline, Lagos, Nigeria (2013–2025), feeding into a Coastal Vulnerability Index (CVI) built in ArcGIS Pro.

MSc Dissertation, Sustainable Water Environments, University of Glasgow.

Built on [COASTGUARD](https://github.com/fmemuir/COASTGUARD) / VedgeSat (Muir et al., 2024), which itself extends [CoastSat](https://github.com/kvos/CoastSat) (Vos et al.).

---

## Contents

- [What this does](#what-this-does)
- [Pipeline overview](#pipeline-overview)
- [Scripts, in run order](#scripts-in-run-order)
- [Key methodological decisions](#key-methodological-decisions)
- [1. Installation](#1-installation)
- [How to run](#how-to-run)
- [Outputs](#outputs)
- [Known gaps / not yet included](#known-gaps--not-yet-included)
- [Citation & acknowledgements](#citation--acknowledgements)

---

## What this does

Starting from one hand-digitised reference line along the Lekki coast, this pipeline:

1. Splits the corridor into manageable segments,
2. extracts vegetation edges and waterlines from Landsat 8 / Sentinel-2 imagery for each segment via Google Earth Engine,
3. casts cross-shore transects and intersects them with those edges to get per-transect time series,
4. merges every segment back into one continuous, deduplicated dataset,
5. computes robust shoreline-change rates per transect and per epoch, and
6. builds a single mappable transect layer, ready to bring into ArcGIS Pro for the CVI itself.

## Pipeline overview

The corridor is split into **13 segments (LEKKI01–13, ~69.46 km)** with 250 m overlap between neighbours, so nothing is missed at a segment boundary. Each segment is processed independently through extraction and transect analysis, then the overlaps are trimmed and everything is stitched back into one `LEKKI` dataset for the change-rate and mapping steps.

```
                 ┌─────────────────────────┐
   Step 1        │ split_refline_into_     │   once, whole corridor
                 │ segments.py             │
                 └───────────┬─────────────┘
                              │  writes Data/LEKKI01.../referenceLines/
                              ▼
        ┌─────────────────────────────────────────┐
        │  per segment (LEKKI01 → LEKKI13):        │
        │                                           │
        │   activate_segment.py  (helper)          │
        │            ↓                             │
        │   VedgeSat_Driver_LEKKI.py    (Step 2)   │
        │            ↓                             │
        │   CoasTrack_Driver_LEKKI.py    (Step 2)  │
        └───────────────────┬───────────────────────┘
                              │
                              ▼
   Step 3        merge_segment_outputs.py      once, after all segments
                              │
                              ▼
   Step 4        epoch_change_stats.py         once — vegetation-edge rates
   Step 5        waterline_change_stats.py     once — waterline rates (not yet in this repo)
                              │
                              ▼
   Step 6        merge_transects_for_mapping.py    once — final mappable layer
                              │
                              ▼
                   ArcGIS Pro: CVI construction (Part A + Part B)
```

## Scripts, in run order

| # | Script | Runs | Purpose |
|---|---|---|---|
| 1 | `split_refline_into_segments.py` | Once | Splits the single hand-digitised reference line into 13 overlapping segment shapefiles, each in its own `Data/<sitename>/referenceLines/` folder. Everything downstream depends on this running first. |
| 1b | `activate_segment.py` | Once per segment, before each segment's Step 2 | COASTGUARD keeps one *shared* `Data/referenceLines/` folder rather than a per-site one — this copies the segment-in-progress's reference line into that shared folder so the drivers below pick up the right one. |
| 2 | `VedgeSat_Driver_LEKKI.py` | Once per segment | Downloads Landsat 8 / Sentinel-2 imagery for the segment's AOI, classifies vegetation, extracts sub-pixel vegetation-edge and waterline positions. The slowest step — hours per segment. |
| — | `CoasTrack_Driver_LEKKI.py` | Once per segment | Casts cross-shore transects along the segment's reference line and intersects them with the extracted vegetation edges, waterlines, waves, and topography, producing the per-transect time series. |
| 3 | `merge_segment_outputs.py` | Once, after all 13 segments | Stitches every segment's veglines, waterlines, transects and intersections into one `LEKKI` dataset — trims the 250 m overlaps (keeping only each segment's "core" territory), and renumbers `TransectID` sequentially along the true coastline distance. |
| 4 | `epoch_change_stats.py` | Once | Computes robust per-epoch and full-period vegetation-edge change rates per transect (Theil-Sen regression, outlier filtering, structure-aware exclusion around the Lekki Deep Sea Port). Produces the `veg_retreat_rate` field used in the CVI. |
| 5 | `waterline_change_stats.py` | Once | Waterline-equivalent of Step 4. **Not yet included in this repo** — see [Known gaps](#known-gaps--not-yet-included). |
| 6 | `merge_transects_for_mapping.py` | Once | Builds the final, single mappable transect shapefile with per-epoch rates joined on as wide columns (`rate_1315`, `rate_1520`, `rate_2025`, `rate_1325`), ready to symbolise directly in ArcGIS Pro. |

## Key methodological decisions

Documented here so they don't live only as comments buried in the code:

- **13-segment corridor.** The validated, submitted corridor is LEKKI01–13 (~69.46 km). Segment numbering was also revised once to reinstate two west segments that an earlier script version had dropped.
- **Not all segments count toward the numbers.** `ALL_SEGMENT_SITES` (LEKKI01–13) is used for veglines/waterlines/raw transects on maps and figures, so the full surveyed coastline is shown honestly. `ANALYSIS_SEGMENT_SITES` (LEKKI05–13) — excluding two untested west segments and two known-poor-quality segments — is what every actual rate, transect-intersection and CVI value is computed from. Maps must symbolise LEKKI01/02 distinctly (hatch/dashed) to show they're shown but not analysed, not silently omitted.
- **Lekki Deep Sea Port is treated as a hard structure, not natural coastline.** Confirmed absent in 2013/2015 imagery, present from 2020. Transects 728–744 have any post-2020 "movement" excluded from natural shoreline-change rates and reported separately, so port construction can't masquerade as erosion or accretion in the site-wide statistics.
- **Rate robustness, three layers:** point-level MAD-based outlier filtering (drops implausible single date-to-date jumps > 60 m) → Theil-Sen (median-of-slopes) regression rather than OLS → a hard ±15 m/yr physical ceiling, followed by a per-epoch, per-structure-group mean ± 2SD cleaning pass.
- **Epochs:** 2013–2015, 2015–2020, 2020–2025, and the full 2013–2025 period.
- **The single `veg_retreat_rate` used in the CVI join** is the mean of available sub-period rates where they exist, falling back to the full-period rate where they don't (Chapter 3, Section 3.6's rule).
- **CRS:** EPSG:32631 (UTM 31N) throughout, for consistent metric distance/area operations.

## 1. Installation

### INSTALL QUICK VERSION

1. Open a command line, navigate to your favoured spot for the repo folder, and download the repo: `git clone https://github.com/Hexcel-Nathan/Lekki_Vegetation_Edge_Dectetion_Analysis.git`
2. Also clone COASTGUARD itself, since this repo's scripts import its `Toolshed` package: `git clone https://github.com/fmemuir/COASTGUARD.git`
3. Navigate into `COASTGUARD` and create the environment: `conda env create -f coastguard_env.yml` (use this repo's copy, or COASTGUARD's own — they're identical)

### INSTALL STEPS SUMMARY

1. Download this repo and COASTGUARD (above)
2. Create conda environment: `conda env create -f coastguard_env.yml`
3. Activate env: `conda activate coastguard`
4. Authenticate GEE: `earthengine authenticate`

**Remember!** Always run `conda activate coastguard` each time you want to use the pipeline. You *should not* need to authenticate `earthengine` each time, just the once when installing.

### 1.1 Download the code

You'll need both this repo and COASTGUARD itself. Either clone them with git (see Quick Version above), or click the green **Code** button on each repo's GitHub page and download + extract the zipped folder. If you download manually, extract to a proper local folder rather than leaving it in Downloads.

### 1.2 Install Miniconda (if you don't already have it)

This pipeline needs Python packages managed through Anaconda/Miniconda. If you don't have either installed:

1. Go to https://www.anaconda.com/download (or https://docs.conda.io/en/latest/miniconda.html for the lighter Miniconda version — recommended, since you don't need the full Anaconda Navigator GUI for this).
2. Download the installer for your OS and run it, accepting the defaults.
3. Restart your terminal (or Anaconda Prompt on Windows) once installation finishes.

### 1.3 Create the conda environment

Once Anaconda/Miniconda is installed:

- **Windows:** open the **Anaconda Prompt** (not PowerShell)
- **Mac/Linux:** open a terminal window

Navigate to wherever you cloned COASTGUARD:

```bash
cd COASTGUARD
```

Then create the environment from the `coastguard_env.yml` file:

```bash
conda update -n base conda
conda env create -f coastguard_env.yml
```

This can take anywhere from a few minutes to a couple of hours depending on your base environment — conda has to resolve every package's dependencies. If it's taking a long time, install [Mamba](https://www.anaconda.com/blog/a-faster-conda-for-a-growing-community) as a faster solver:

```bash
conda update -n base conda
conda install -n base conda-libmamba-solver
conda config --set solver libmamba
```

### 1.4 Activate the environment

Every time you want to run any part of this pipeline, activate the environment first:

```bash
conda activate coastguard
cd coastguard
```

### 1.5 Activate the Google Earth Engine API

This pipeline uses Google Earth Engine (GEE) to pull satellite imagery. You need GEE API access:

1. Sign up at https://signup.earthengine.google.com/ with a Google account (select "research" as your intended use). Approval can take up to 24 hours, usually faster.
2. While you wait, install the Google Cloud Command Line Interface (gcloud CLI) — instructions at https://cloud.google.com/sdk/docs/install.
3. Once approved, open a terminal, `conda activate coastguard`, then run:
   ```bash
   earthengine authenticate
   ```
4. A browser window opens — log in with the same Google account you used to sign up for GEE. It should redirect back to your terminal automatically; if not, paste the authorization code shown into the terminal.

You shouldn't need to repeat this step on future runs — just the one-time authentication.

## How to run

```bash
conda activate coastguard

# Step 1 — once
python split_refline_into_segments.py

# Steps 1b + 2, per segment — repeat for LEKKI05 through LEKKI13
# (LEKKI01-04 can be run too, for map completeness, but are excluded from analysis)
python activate_segment.py LEKKI05
python VedgeSat_Driver_LEKKI.py        # edit sitename = 'LEKKI05' inside first
python CoasTrack_Driver_LEKKI.py       # edit sitename = 'LEKKI05' inside first
# ... repeat for each segment ...

# Step 3 — once, after every segment above is done
python merge_segment_outputs.py

# Steps 4 & 5 — once each, independent of each other
python epoch_change_stats.py
python waterline_change_stats.py       # not yet in this repo

# Step 6 — once, after Steps 3 and 4
python merge_transects_for_mapping.py
```

Then continue in **ArcGIS Pro** with the CVI/Buffer guide (Part A, then Part B), using `LEKKI_Mapping_Transects.shp` plus `veg_rate_for_cvi_join.csv` and `waterline_rate_for_cvi_join.csv`.

## Outputs

Everything lands under `Data/<sitename>/`:

- `lines/` — extracted veglines, waterlines, transects (per segment, then merged)
- `intersections/` — transect–vegline / transect–waterline / transect–wave / transect–topo intersection pickles
- `vegedge_change_by_epoch_natural.csv` — Table 4.1: mean/min/max rate and % retreating/advancing/stable, per epoch, natural transects only
- `vegedge_change_by_epoch_lekki_deep_sea_port.csv` — same, for the excluded structure-affected transects
- `veg_rate_for_cvi_join.csv` — the single per-transect `veg_retreat_rate` to join onto the CVI
- `LEKKI_Mapping_Transects.shp` — final shapefile for ArcGIS Pro, with wide-format per-epoch rate/status columns

## Known gaps / not yet included

- **`waterline_change_stats.py` (Step 5)** is referenced throughout this pipeline but isn't in this repo yet — it's the waterline equivalent of Step 4, and Steps 5/6 downstream expect its `waterline_rate_for_cvi_join.csv` output.
- **`CoasTrack_Driver_LEKKI.py` has two placeholder paths** that need setting per run: `TIF = '/path/to/Slope_Raster.tif'` (topography intersection) and `ValidationShp = './Validation/StAndrews_Veg_Edge_combined_2007_2022_singlepart.shp'` (currently pointing at an example St Andrews validation set, not Lekki-specific validation data).

## Citation & acknowledgements

This pipeline is built directly on:

- Muir, F. M. E., Hurst, M. D., Richardson-Foulger, L., Naylor, L. A., Rennie, A. F. (2024). VedgeSat: An automated, open-source toolkit for coastal change monitoring using satellite-derived vegetation edges. *Earth Surface Processes and Landforms, 49*(8), 2405–2423. https://doi.org/10.1002/esp.5835
- Muir, F. M. E. (2023). COASTGUARD. GitHub. https://github.com/fmemuir/COASTGUARD
- Vos, K. et al. CoastSat. https://github.com/kvos/CoastSat

If you use or adapt this Lekki-specific pipeline, please cite the dissertation (details to be added on submission) alongside the above.
