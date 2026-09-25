# epoch_change_stats.py
# Computes per-epoch rates of vegetation-edge change for every transect,
# using the FULL dense archive of detections (not reduced to annual snapshots),
# with three layers of protection:
#   1. Point-level filtering: implausible single date-distance jumps are
#      dropped from the raw series before fitting (robust median-deviation check).
#   2. Robust regression: Theil-Sen (median-of-slopes) instead of OLS.
#   3. Structure-aware epoching: any set of transects crossing a hard
#      structure (harbor, jetty, etc.) built partway through the record is
#      no longer tracking natural shoreline change from that structure's
#      construction date onward - they're tracking a hard structure edge
#      (or nothing at all). Any epoch/rate that would blend or represent
#      post-construction "movement" for these transects is NOT reported as
#      a natural shoreline-change rate; it is flagged instead, so it can
#      never silently pollute the mean/summary stats. Generalised below to
#      handle ANY NUMBER of such structures, not just one.

print("=" * 70)
print("RUNNING: waterline_change_stats_FINAL.py - WATERLINE, THEIL-SEN + MULTI-STRUCTURE-AWARE VERSION")
print("(point-level MAD filtering + Theil-Sen regression + per-epoch mean+/-2SD")
print(" cutoff + structure-affected transects excluded from natural rates,")
print(" per hard structure defined in STRUCTURES below)")
print("If you don't see this banner, you are running an old/stale copy of this file.")
print("=" * 70)
print()

import pickle
import numpy as np
import pandas as pd
from scipy.stats import theilslopes

sitename = 'LEKKI'  # matches the renamed MERGED_SITE in merge_segment_outputs_2.py -
                         # this used to be 'LEKKI', which now refers to a stale/old
                         # partial merge, not your full 19-segment corridor.
with open(f'Data/{sitename}/intersections/{sitename}_transect_water_intersects.pkl', 'rb') as f:
    TransectInterGDF = pickle.load(f)

epochs = [('2013-01-01', '2015-12-31'),
          ('2015-01-01', '2020-12-31'),
          ('2020-01-01', '2025-12-31'),
          ('2013-01-01', '2025-12-31')]
epoch_labels = ['2013-2015', '2015-2020', '2020-2025', '2013-2025']

POINT_JUMP_THRESHOLD_M = 60   # a single date-to-date jump bigger than this (m) is
                               # treated as an implausible detection, not real change
POINT_Z_THRESHOLD = 6

# Hard physical ceiling on epoch RATES (not raw points): no real vegetation-edge
# migration rate at this site is credible above this, regardless of what the
# per-epoch mean+/-2SD distribution would otherwise tolerate. Applied BEFORE
# the mean+/-2SD pass, so a noisy epoch can't let an implausible rate hide
# inside a wide distribution.
RATE_HARD_CUTOFF_M_YR = 15

# --- Hard-structure configuration ------------------------------------------
# One entry per real, physically distinct hard structure that overrides
# natural vegetation-edge dynamics for the transects crossing it. Each
# structure gets its own transect set AND its own construction/appearance
# date - do NOT assume every structure shares Lekki Port's 2020 date.
#
# Add a new structure here whenever you confirm one (empty 'transects' sets
# are skipped safely, so it's fine to leave a placeholder until you've
# identified the real TransectID range).
STRUCTURES = [
    {
        "name": "Lekki Deep Sea Port",
        "transects": set(range(728, 745)),   # 728 to 744 inclusive - confirmed
        "date": pd.Timestamp('2020-01-01'),  # construction start
    },
    {
        "name": "Dangote Refinery Jetty",
        "transects": set(range(1037, 1041)),  # 1037 to 1040 inclusive - confirmed
        "date": pd.Timestamp('2017-01-01'),   # confirmed construction date
    },
    # Add further structures here in the same shape as needed.
]

# Fast lookup: TransectID -> structure dict (or None if unaffected by any)
_TRANSECT_TO_STRUCTURE = {}
for _struct in STRUCTURES:
    for _tid in _struct["transects"]:
        if _tid in _TRANSECT_TO_STRUCTURE:
            raise ValueError(
                f"TransectID {_tid} is claimed by more than one structure "
                f"({_TRANSECT_TO_STRUCTURE[_tid]['name']!r} and {_struct['name']!r}) - "
                "a transect can only cross one hard structure. Fix STRUCTURES above."
            )
        _TRANSECT_TO_STRUCTURE[_tid] = _struct


def structure_for(tid):
    """Returns the structure dict this TransectID is affected by, or None."""
    return _TRANSECT_TO_STRUCTURE.get(tid)


# --- Sanity check before trusting any STRUCTURES entry above --------------
# Each structure's TransectID set was identified against a PAST merge run.
# Since merge_segment_outputs_2.py fully re-derives TransectID from scratch
# every run (0..N-1, in ANALYSIS_SEGMENT_SITES list order), a structure's
# transect numbers are only still correct if segment order/inclusion hasn't
# changed relative to when they were identified. This prints which
# segment(s) each defined structure's transects actually landed in - if a
# structure shows more than one segment, or a segment you don't recognise,
# STOP and re-derive that structure's transect set before trusting anything
# downstream.
if 'source_segment' in TransectInterGDF.columns:
    for struct in STRUCTURES:
        if not struct["transects"]:
            print(f"'{struct['name']}': transect set is empty - skipping sanity check "
                  "(fill this in once identified, see STRUCTURES above).\n")
            continue
        check = TransectInterGDF[TransectInterGDF['TransectID'].isin(struct["transects"])]
        print(f"Sanity check - segment(s) '{struct['name']}' transects actually fall in:")
        print(check['source_segment'].value_counts())
        print(
            "(Expect ONE segment name here. More than one, or an unrecognised "
            f"segment, means this structure's transect set no longer points at "
            "the right place - re-derive it from source_segment/local_TransectID.)\n"
        )
else:
    print(
        "WARNING: 'source_segment' column not found in the merged pickle - "
        "cannot verify any STRUCTURES entry still points at the right place. "
        "Proceed only if you've confirmed this some other way.\n"
    )


def filter_point_outliers(dates_sorted, dists_sorted):
    """Drop individual points whose distance value jumps implausibly far from
    the local median - catches single bad detections without discarding the
    whole transect/epoch."""
    if len(dists_sorted) < 3:
        return dates_sorted, dists_sorted
    dists = np.array(dists_sorted, dtype=float)
    med = np.median(dists)
    mad = np.median(np.abs(dists - med)) or 1.0
    z = np.abs(dists - med) / (1.4826 * mad)
    keep = z < POINT_Z_THRESHOLD
    return [d for d, k in zip(dates_sorted, keep) if k], dists[keep]


def resolve_epoch_window(tid, start, end):
    """
    For a transect affected by any defined hard structure, decide whether
    this epoch is usable at all, given that structure's own appearance date.

    An epoch is only usable for a structure-affected transect if it ends
    entirely before that structure's date. Any epoch that overlaps or
    postdates it is excluded outright, no truncation attempt (truncating to
    a partial pre-construction window was tried previously and consistently
    failed - too few clean points to fit - so it isn't worth the complexity).

    Returns (eval_start, eval_end, flag):
      flag == 'natural'            - unaffected by any structure, or this
                                      epoch fully predates the relevant
                                      structure's date -> evaluate as-is
      flag == 'structure_excluded' - affected transect, epoch overlaps or
                                      postdates that structure's date ->
                                      rate forced to NaN, not a natural
                                      shoreline signal
    """
    struct = structure_for(tid)
    if struct is None:
        return start, end, 'natural'

    if end < struct["date"]:
        # Whole epoch is before this structure existed - genuinely natural
        return start, end, 'natural'
    else:
        return start, end, 'structure_excluded'


def epoch_rate(tid, dates, distances, start, end):
    """Returns (rate, flag). rate is np.nan wherever a natural rate can't or
    shouldn't be computed (too few points, too short a span, or the epoch is
    structure_excluded for a structure-affected transect)."""
    eval_start, eval_end, flag = resolve_epoch_window(tid, start, end)

    if flag == 'structure_excluded':
        return np.nan, flag

    dates = pd.to_datetime(dates)
    mask = (dates >= eval_start) & (dates <= eval_end)
    if mask.sum() < 3:
        return np.nan, flag

    d_sub = dates[mask]
    y_sub = np.array(distances)[mask]
    order = np.argsort(d_sub.values)
    d_sorted = d_sub.values[order]
    y_sorted = y_sub[order]

    d_clean, y_clean = filter_point_outliers(list(d_sorted), y_sorted)
    if len(y_clean) < 3:
        return np.nan, flag

    t_years = (pd.to_datetime(d_clean) - pd.to_datetime(d_clean).min()).days / 365.25
    if t_years.max() - t_years.min() < 0.5:
        return np.nan, flag

    # Theil-Sen: robust to outliers, unlike ordinary least-squares linregress
    slope, intercept, lo_slope, hi_slope = theilslopes(y_clean, t_years)
    return -slope, flag  # negated so that negative = landward retreat


records = []
for _, row in TransectInterGDF.iterrows():
    tid = row['TransectID']
    dates = row['wldates']   # <-- waterline field name, NOT 'dates'
    dists = row['wldists']   # <-- waterline field name, NOT 'distances'
    struct = structure_for(tid)
    struct_name = struct["name"] if struct is not None else None
    for (start, end), label in zip(epochs, epoch_labels):
        rate, flag = epoch_rate(tid, dates, dists, pd.Timestamp(start), pd.Timestamp(end))
        records.append({'TransectID': tid, 'epoch': label, 'rate_m_yr': rate,
                         'flag': flag, 'structure': struct_name})

FullRateDF = pd.DataFrame(records)  # keep everything, including NaN/excluded rows, for transparency
FullRateDF.to_csv(f'Data/{sitename}/waterline_change_per_transect_ALL_including_excluded.csv', index=False)

# Report exactly what happened per epoch, per structure, before dropping NaNs
print("Structure-affected transect epoch handling:")
for struct in STRUCTURES:
    if not struct["transects"]:
        continue
    struct_rows = FullRateDF[FullRateDF['structure'] == struct["name"]]
    print(f"  '{struct['name']}':")
    for label in epoch_labels:
        epoch_rows = struct_rows[struct_rows['epoch'] == label]
        for flag, n in epoch_rows['flag'].value_counts().items():
            print(f"    {label}: {n} transects -> '{flag}'")
print()

RateDF = FullRateDF.dropna(subset=['rate_m_yr']).copy()

# --- Step 1: hard physical cutoff, applied BEFORE any distribution-based
# filtering, on ALL rows (natural and structure-affected alike) ---
n_before_hard = len(RateDF)
hard_outliers = RateDF[RateDF['rate_m_yr'].abs() > RATE_HARD_CUTOFF_M_YR]
if len(hard_outliers):
    print(f"Hard cutoff: removing {len(hard_outliers)} of {n_before_hard} rates with "
          f"|rate| > {RATE_HARD_CUTOFF_M_YR} m/yr (implausible, not a real cutoff-fit).")
RateDF = RateDF[RateDF['rate_m_yr'].abs() <= RATE_HARD_CUTOFF_M_YR].copy()

# --- Step 2: data-driven safety net (true mean +/- 2 SD), computed PER EPOCH,
# and separately per structure-group (natural, or each named structure) so a
# truncated structure-affected rate can't get judged against a distribution
# it doesn't belong to (and vice versa). This runs on the already-hard-cutoff
# data, so mu/sigma aren't themselves dragged around by the extreme values
# just removed. ---
n_before = len(RateDF)
cleaned_parts = []
RateDF['structure_group'] = RateDF['structure'].fillna('natural')
for epoch, group in RateDF.groupby('epoch'):
    for group_name, subgroup in group.groupby('structure_group'):
        if len(subgroup) < 2:
            cleaned_parts.append(subgroup)
            continue
        mu, sigma = subgroup['rate_m_yr'].mean(), subgroup['rate_m_yr'].std()
        lo, hi = mu - 2 * sigma, mu + 2 * sigma
        outliers = subgroup[(subgroup['rate_m_yr'] < lo) | (subgroup['rate_m_yr'] > hi)]
        if len(outliers):
            print(f"{epoch} ({group_name}): removing {len(outliers)} of {len(subgroup)} rates "
                  f"outside mean +/- 2SD = [{lo:.2f}, {hi:.2f}] m/yr")
        cleaned_parts.append(subgroup[(subgroup['rate_m_yr'] >= lo) & (subgroup['rate_m_yr'] <= hi)])
RateDF = pd.concat(cleaned_parts, ignore_index=True)
print(f"\n{len(RateDF)} of {n_before_hard} epoch-transect rates retained overall "
      f"(after hard cutoff + mean+/-2SD).\n")


def classify(rate):
    if rate < -0.5:
        return 'retreating'
    elif rate > 0.5:
        return 'advancing'
    else:
        return 'stable'


RateDF['status'] = RateDF['rate_m_yr'].apply(classify)

# --- Main summary table: NATURAL transects only ---------------------------
# This is the number you'd actually quote as "site-wide" shoreline change -
# it must not be diluted by any structure-affected rows.
NaturalRateDF = RateDF[RateDF['structure'].isna()]

summary = NaturalRateDF.groupby('epoch').agg(
    mean_rate=('rate_m_yr', 'mean'),
    min_rate=('rate_m_yr', 'min'),
    max_rate=('rate_m_yr', 'max'),
    n=('rate_m_yr', 'count')
)
status_pct = (NaturalRateDF.groupby(['epoch', 'status']).size()
              .unstack(fill_value=0)
              .apply(lambda r: 100 * r / r.sum(), axis=1))

table_4_1 = summary.join(status_pct).round(2)
table_4_1 = table_4_1.reindex(epoch_labels)
print("Table 4.1 (natural transects only, all defined structures excluded):")
print(table_4_1)
table_4_1.to_csv(f'Data/{sitename}/waterline_change_by_epoch_natural.csv')

# --- Companion tables: one per structure, for reporting/mapping separately -
# Only epochs fully before that structure's date will have real values;
# every epoch overlapping or postdating it is NaN by design (see
# resolve_epoch_window).
for struct in STRUCTURES:
    if not struct["transects"]:
        continue
    StructRateDF = RateDF[RateDF['structure'] == struct["name"]]
    if len(StructRateDF):
        struct_summary = StructRateDF.groupby('epoch').agg(
            mean_rate=('rate_m_yr', 'mean'),
            min_rate=('rate_m_yr', 'min'),
            max_rate=('rate_m_yr', 'max'),
            n=('rate_m_yr', 'count')
        ).reindex(epoch_labels)
        safe_name = struct["name"].lower().replace(" ", "_")
        print(f"\n'{struct['name']}' transects - report/map separately. "
              f"Only epochs fully before {struct['date'].date()} are genuine natural rates:")
        print(struct_summary)
        struct_summary.to_csv(f'Data/{sitename}/waterline_change_by_epoch_{safe_name}.csv')

RateDF.to_csv(f'Data/{sitename}/waterline_change_per_transect_new.csv', index=False)

# ----------------------------------------------------------------------
# Single per-transect waterline_rate for the CVI join, per Section 3.6's
# own rule: mean of available sub-period EPR values, or the full-period
# LRR alone where sub-period rates are unavailable.
# ----------------------------------------------------------------------
subperiods = ['2013-2015', '2015-2020', '2020-2025']
full_period = '2013-2025'

pivot = RateDF.pivot_table(index='TransectID', columns='epoch', values='rate_m_yr')
for col in subperiods + [full_period]:
    if col not in pivot.columns:
        pivot[col] = np.nan

sub_mean = pivot[subperiods].mean(axis=1, skipna=True)
waterline_rate = sub_mean.where(sub_mean.notna(), pivot[full_period])

cvi_join = waterline_rate.reset_index()
cvi_join.columns = ['TransectID', 'waterline_rate']
cvi_join.to_csv(f'Data/{sitename}/waterline_rate_for_cvi_join.csv', index=False)
print(f"\nSaved -> Data/{sitename}/waterline_rate_for_cvi_join.csv "
      f"({cvi_join['waterline_rate'].notna().sum()} of {len(cvi_join)} transects have a usable rate)")
print("This is the file to join onto your transect layer for the CVI's waterline_rate field.")
