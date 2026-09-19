#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
STEP 1 of 6 IN THE FULL PIPELINE - split_refline_into_segments.py
===============================================================================
PURPOSE: Splits ONE continuous, hand-digitised reference-line shapefile into
N sub-segments, each written to its own COASTGUARD site folder
(Data/<sitename>/referenceLines/), ready for VedgeSat/CoasTrack processing
(Step 2). Run this BEFORE anything else - everything downstream depends on
these segment folders existing.

WHY SPLIT AT ALL?
Toolbox.AOIfromLine() turns your WHOLE reference line into a single Earth
Engine bounding-box query. A long line means a huge AOI, which massively
increases GEE image-tile count and processing time, and risks GEE's
per-request size/pixel limits. Splitting into ~5 km segments keeps every
run's AOI small, fast, and inside GEE's limits.

===============================================================================
HOW THIS WAS USED FOR THIS PROJECT:
===============================================================================
ONE run, producing the full LEKKI01-13 corridor directly from a single
continuous, hand-digitised reference line:
    INPUT_SHP   = "Data/referenceLines/LekkiFullCorridor.shp"
    N_SEGMENTS  = 13
    START_INDEX = 1
    OFFSET_KM   = 0.0

Edit CHUNK 2 below if you need to re-run this, then execute the whole
script.

===============================================================================
REPRODUCIBILITY NOTE:
===============================================================================
This script measures distance along the corridor starting from 0 at the
beginning of the single continuous reference line given as INPUT_SHP, so
core_start_km/core_end_km for every one of the 13 output segments are
correct and consistent with each other directly out of this one run.

Run with: (coastguard) $ python split_refline_into_segments.py
"""

# ----------------------------------------------------------------------
# CHUNK 1: Imports
# ----------------------------------------------------------------------
import os
import pandas as pd
import geopandas as gpd
from shapely.ops import linemerge, substring


# ----------------------------------------------------------------------
# CHUNK 2: EDIT ME - settings for this run
# ----------------------------------------------------------------------
INPUT_SHP = "Data/referenceLines/LekkiFullCorridor.shp"   # <-- EDIT if path differs
N_SEGMENTS = 13                                    # <-- full corridor, one run
SITE_PREFIX = "LEKKI"
START_INDEX = 1
OFFSET_KM = 0.0
OUTPUT_ROOT = "Data"

OVERLAP_M = 250            # metres of deliberate overlap between ADJACENT segments.
                            # merge_segment_outputs_2.py (Step 3) trims this back out
                            # later, keeping only the midpoint of each overlap - this
                            # exists purely so vegetation-edge detection right at a
                            # segment boundary isn't missed.
PROJECTED_EPSG = 32631      # UTM 31N - matches Toolbox.FindUTM elsewhere in this project


# ----------------------------------------------------------------------
# CHUNK 3: Load and merge into a single continuous LineString
# ----------------------------------------------------------------------
def load_single_line(path):
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise ValueError(f"{path} has no CRS defined - set it to EPSG:4326 in QGIS first.")
    union_geom = gdf.geometry.union_all()
    merged_geom = union_geom if union_geom.geom_type == "LineString" else linemerge(union_geom)
    if merged_geom.geom_type != "LineString":
        raise ValueError(
            "Reference line is not one continuous LineString after merging. "
            "Check for gaps/disconnected pieces in QGIS before splitting."
        )
    return gdf, merged_geom


# ----------------------------------------------------------------------
# CHUNK 4: Cut the line into N overlapping segments
# ----------------------------------------------------------------------
def make_segments(line_geom, n_segments, overlap_m, start_index, offset_km):
    total_length = line_geom.length
    seg_length = total_length / n_segments
    offset_m = offset_km * 1000

    segments = []
    for i in range(n_segments):
        start = i * seg_length
        end = (i + 1) * seg_length
        start_ext = max(0.0, start - overlap_m / 2) if i > 0 else 0.0
        end_ext = min(total_length, end + overlap_m / 2) if i < n_segments - 1 else total_length

        segments.append({
            "seg_id": i + start_index,
            "sitename": f"{SITE_PREFIX}{i + start_index:02d}",
            "start_m": start_ext + offset_m,
            "end_m": end_ext + offset_m,
            "core_start_m": start + offset_m,
            "core_end_m": end + offset_m,
            "length_km": (end_ext - start_ext) / 1000,
            "geometry": substring(line_geom, start_ext, end_ext),
        })
    return segments


# ----------------------------------------------------------------------
# CHUNK 5: Write each segment to its own COASTGUARD site folder
# ----------------------------------------------------------------------
def write_segments(segments, source_crs):
    records = []
    for seg in segments:
        site_folder = os.path.join(OUTPUT_ROOT, seg["sitename"], "referenceLines")
        os.makedirs(site_folder, exist_ok=True)

        seg_gdf = gpd.GeoDataFrame(
            {"seg_id": [seg["seg_id"]], "sitename": [seg["sitename"]]},
            geometry=[seg["geometry"]], crs=f"EPSG:{PROJECTED_EPSG}",
        ).to_crs("EPSG:4326")

        # NOTE: filename is "Lagos_RefLine.shp" (with underscore) - this exact
        # name is what VedgeSat_Driver_LEKKI.py (Step 2) expects. A naming
        # mismatch here was a real bug caught during this project - don't
        # rename this without also updating Step 2's referenceLineShp setting.
        out_path = os.path.join(site_folder, "Lagos_RefLine.shp")
        seg_gdf.to_file(out_path)
        print(f"  {seg['sitename']}: {seg['length_km']:.2f} km  ->  {out_path}")

        records.append({
            "sitename": seg["sitename"],
            "start_km": seg["start_m"] / 1000, "end_km": seg["end_m"] / 1000,
            "core_start_km": seg["core_start_m"] / 1000, "core_end_km": seg["core_end_m"] / 1000,
            "length_km": seg["length_km"], "shapefile": out_path,
        })
    return pd.DataFrame(records)


# ----------------------------------------------------------------------
# CHUNK 6: Main - run everything, save a summary CSV
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print(f"Loading and merging {INPUT_SHP} ...")
    gdf_wgs84, line_wgs84 = load_single_line(INPUT_SHP)

    print(f"Reprojecting to EPSG:{PROJECTED_EPSG} for metric splitting ...")
    line_proj = gpd.GeoSeries([line_wgs84], crs=gdf_wgs84.crs).to_crs(f"EPSG:{PROJECTED_EPSG}").iloc[0]

    print(f"Total reference line length: {line_proj.length/1000:.2f} km")
    print(f"Splitting into {N_SEGMENTS} segments (with {OVERLAP_M} m internal overlap) ...")
    segments = make_segments(line_proj, N_SEGMENTS, OVERLAP_M, START_INDEX, OFFSET_KM)

    print("Writing segment shapefiles:")
    summary_df = write_segments(segments, gdf_wgs84.crs)

    summary_path = os.path.join(OUTPUT_ROOT, "refline_segments_summary.csv")
    summary_df.to_csv(summary_path, index=False)
    print(f"\nSaved segment summary to {summary_path}")
    print(f"\nNext step: run VedgeSat_Driver_LEKKI.py once per segment "
          f"(sitename = 'LEKKI01' through 'LEKKI13' in turn).")
