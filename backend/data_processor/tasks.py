import pandas as pd
import numpy as np
import datetime
from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import RawLineData, ChangeoverSummary, RecipeMaster, StandardTimeMaster, MaterialMaster, ChangeoverRule
from datetime import timedelta
from tqdm import tqdm  
import os

def resolve_changeover(prev_recipe, curr_recipe, prev_type, curr_type, custom_rules, base_rules):
    # 1. Check custom overrides in order of specificity
    
    # a. Exact Match
    if (prev_recipe, curr_recipe) in custom_rules:
        return custom_rules[(prev_recipe, curr_recipe)]
        
    # b. To-Wildcard (e.g. ANY -> CPJ512)
    if ('ANY', curr_recipe) in custom_rules:
        return custom_rules[('ANY', curr_recipe)]
        
    # c. From-Wildcard (e.g. CPJ114 -> ANY)
    if (prev_recipe, 'ANY') in custom_rules:
        return custom_rules[(prev_recipe, 'ANY')]
        
    # d. Double-Wildcard (e.g. ANY -> ANY)
    if ('ANY', 'ANY') in custom_rules:
        return custom_rules[('ANY', 'ANY')]
    # 2. Fallback to base types
    if not prev_type or not curr_type:
        return None
        
    prev_type_str = str(prev_type).lower()
    curr_type_str = str(curr_type).lower()
    
    base_match = None
    if prev_type_str == 'steel' and curr_type_str == 'steel':
        base_match = 'Steel to Steel'
    elif prev_type_str == 'steel' and curr_type_str == 'fabric':
        base_match = 'Steel to Fabric'
    elif prev_type_str == 'fabric' and curr_type_str == 'steel':
        base_match = 'Fabric to Steel'
    elif prev_type_str == 'fabric' and curr_type_str == 'fabric':
        base_match = 'Fabric to Fabric'
        
    if base_match and base_match in base_rules:
        return base_rules[base_match]
        
    return 'Unknown'


# Use local timezone everywhere
LOCAL_TZ = 'Asia/Kolkata'


def to_local_ts(val):
    """Convert a value to a pandas.Timestamp localized to LOCAL_TZ.
    Handles naive and already-aware inputs.
    """
    ts = pd.to_datetime(val)
    try:
        # pandas Timestamp
        if ts.tzinfo is None:
            ts = ts.tz_localize(LOCAL_TZ)
        else:
            ts = ts.tz_convert(LOCAL_TZ)
    except Exception:
        # Fallback for numpy datetime64 or others
        ts = pd.Timestamp(ts).tz_localize(LOCAL_TZ)
    return ts


# Matching rules live in data_processor/recipe_codes.py so the pipeline, the
# views and the management commands cannot drift apart. Aliased here because
# this name is used throughout the module.
from .recipe_codes import normalize_recipe_code as _normalize_recipe_code
from .recipe_codes import upsert_master_recipe


def _master_spelling(value, display_map):
    """Return the recipe code as RecipeMaster spells it.

    Codes are normalised (spaces stripped, upper-cased) for MATCHING, because
    the same recipe arrives from the sensors in several spellings. But the
    normalised form is not what operators recognise, and it will not compare
    equal to the dropdown values the UI builds from RecipeMaster.

    So the summary stores the master's spelling. Recipes with no master row
    keep their normalised code, which is the only form we have for them.
    """
    normalized = _normalize_recipe_code(value)
    if not normalized:
        return value
    return display_map.get(normalized, value)


LINE_PASS_CODE = _normalize_recipe_code('LINE PASS')


# ======================================================================
# == B-POINT IDENTIFICATION (SETUP START)
# ======================================================================

def B_point(p2, last, roll_batch):
    """
    Ported logic from BpointIdentify_Script.py.
    Finds the 'SETUP START' point (B-Point).
    Returns np.nan when not found.
    """
    # Subset from p2 .. last (these are integer positions after reset_index)
    AtoLast_df = roll_batch.loc[p2:last, :]
    if AtoLast_df.empty:
        return np.nan

    all_idx = np.sort(AtoLast_df.index)[::-1]
    # window start points: indices >= min(all_idx) + 15
    try:
        min_idx = int(min(all_idx))
    except Exception:
        return np.nan
    window_start_points = np.sort([idx for idx in all_idx if idx >= min_idx + 15])[::-1]

    LS_10_idx = np.sort(AtoLast_df.loc[AtoLast_df['LS>=10'] == 1, :].index)[::-1]

    # Step-1: check for LS>=10 sustained spikes then find first LS DROP after them
    B_candidate_1 = np.nan
    if any(AtoLast_df['LS>=10'] == 1):
        for idx in LS_10_idx:
            prev_idx = idx - 1
            # if previous index also in LS_10_idx (sustained)
            if prev_idx in LS_10_idx:
                LS10toLast_df = roll_batch.loc[idx:last, :]
                if not LS10toLast_df.loc[LS10toLast_df['LS DROP'] == 1, :].empty:
                    B_candidate_1 = LS10toLast_df.loc[LS10toLast_df['LS DROP'] == 1, :].index.min()
                break
            else:
                if not AtoLast_df.loc[AtoLast_df['LS DROP'] == 1, :].empty:
                    B_candidate_1 = AtoLast_df.loc[AtoLast_df['LS DROP'] == 1, :].index.min()
    else:
        if not AtoLast_df.loc[AtoLast_df['LS DROP'] == 1, :].empty:
            B_candidate_1 = AtoLast_df.loc[AtoLast_df['LS DROP'] == 1, :].index.min()

    # Step-2: Check for 15-sample window where LS>=4 exists leading to LS DROP
    B_candidate_2 = np.nan
    if len(AtoLast_df.index) < 16:
        if not AtoLast_df.loc[AtoLast_df['LS DROP'] == 1, :].empty:
            B_candidate_2 = AtoLast_df.loc[AtoLast_df['LS DROP'] == 1, :].index.min()
    else:
        for start_point in window_start_points:
            end_point = start_point - 15
            # Ensure end_point present in index
            if end_point < 0 or start_point not in roll_batch.index or end_point not in roll_batch.index:
                continue
            # check sustained LS>=4 over window end_point..start_point
            if all(roll_batch.loc[end_point:start_point, 'LS>=4'] == 1):
                LS4toLast_df = roll_batch.loc[start_point:last, :]
                if not LS4toLast_df.loc[LS4toLast_df['LS DROP'] == 1, :].empty:
                    B_candidate_2 = LS4toLast_df.loc[LS4toLast_df['LS DROP'] == 1, :].index.min()
                break

    # Normalize return: keep np.nan as "not found"
    if pd.isna(B_candidate_1) and pd.isna(B_candidate_2):
        return np.nan
    if pd.isna(B_candidate_1):
        return B_candidate_2
    if pd.isna(B_candidate_2):
        return B_candidate_1
    return B_candidate_1 if B_candidate_1 >= B_candidate_2 else B_candidate_2


# ======================================================================
# == C-POINT IDENTIFICATION (RAMP UP)
# ======================================================================

def C_point(first, p1, roll_batch):
    """
    Ported logic from CpointIdentify_Script.py.
    Finds the 'RAMP UP' point (C-Point).
    Returns np.nan when not found.
    """

    FirsttoD_df = roll_batch.loc[first:p1, :]

    # Legacy padded 15 rows unconditionally; here we pad only if non-empty (safer)
    if not FirsttoD_df.empty:
        # create 15 synthetic indices right after max index
        test_idx = [idx for idx in range(int(FirsttoD_df.index.max()) + 1, int(FirsttoD_df.index.max()) + 16)]
        test = pd.DataFrame({'LS>=4': 15 * [1]}, index=test_idx)
        FirsttoD_df = pd.concat([FirsttoD_df, test])

    if FirsttoD_df.empty:
        return np.nan

    all_idx = np.sort(FirsttoD_df.index)
    window_start_points = np.sort([idx for idx in all_idx if idx <= max(all_idx) - 15])

    C = np.nan
    if len(FirsttoD_df.index) < 16:
        if not FirsttoD_df.loc[FirsttoD_df['LS>=4'] == 1, :].empty:
            C = FirsttoD_df.loc[FirsttoD_df['LS>=4'] == 1, :].index.min()
    else:
        for start_point in window_start_points:
            end_point = start_point + 15
            # validate indices in the original roll_batch
            if start_point not in roll_batch.index or end_point not in roll_batch.index:
                continue
            if all(roll_batch.loc[start_point:end_point, 'LS>=4'] == 1):
                window = roll_batch.loc[start_point:end_point, :]
                if not window.loc[window['LS>=4'] == 1, :].empty:
                    C = window.loc[window['LS>=4'] == 1, :].index.min()
                break

    return C


# ======================================================================
# == TIME LOSS CALCULATION (AUC method)
# ======================================================================

def calculate_time_loss(batch, roll2, batch_first):
    """
    Calculates ramp up loss, ramp down loss, and total setup time.

    Returns:
        (ramp_up_loss, ramp_down_loss, total_setup_time, static_setup, ramp_up_time)

    Defensive: handles empty windows, zero target speed and timezone-aware timestamps.
    """
    try:
        batch_first_batch = batch_first[batch_first['BATCH'] == batch]
        if batch_first_batch.empty:
            return np.nan, np.nan, np.nan, np.nan, np.nan

        # Local-aware timestamps
        ramp_up_start_time = to_local_ts(batch_first_batch['RAMP_UP'].iloc[0])
        setup_complete_time = to_local_ts(batch_first_batch['SETUP_COMPLETE'].iloc[0])
        setup_start_time = to_local_ts(batch_first_batch['SETUP_START'].iloc[0])
        ramp_down_time = to_local_ts(batch_first_batch['RAMP_DOWN'].iloc[0])

        # --- Ramp Up Time Loss (z) ---
        z = roll2[(roll2['DATE TIME'] >= ramp_up_start_time) &
                  (roll2['DATE TIME'] <= setup_complete_time)].copy()

        if z.empty or 'Target Speed' not in z.columns or z['Target Speed'].min() == 0:
            time_loss = np.nan
            ramp_up_time = np.nan
        else:
            z['Series'] = z['DATE TIME'].diff().apply(lambda x: x.total_seconds() / 60).cumsum().fillna(0)
            auc = np.trapz(z['Line_Speed_Act'], x=z['Series'])
            time_taken = auc / z['Target Speed'].min()
            time_loss = round(z['Series'].max() - time_taken, 2)
            ramp_up_time = round(z['Series'].max(), 1)

        # --- Static Setup Time (y) ---
        y = roll2[(roll2['DATE TIME'] >= setup_start_time) &
                  (roll2['DATE TIME'] <= ramp_up_start_time)].copy()

        static_setup = np.nan
        if not y.empty:
            y['Series'] = y['DATE TIME'].diff().apply(lambda x: x.total_seconds() / 60).cumsum().fillna(0)
            static_setup = round(y['Series'].max(), 1)

        # --- Ramp Down Time Loss (x) ---
        x = roll2[(roll2['DATE TIME'] >= ramp_down_time) &
                  (roll2['DATE TIME'] <= setup_start_time)].copy()

        if x.empty or 'Target Speed' not in x.columns or x['Target Speed'].min() == 0:
            x_time_loss = np.nan
        else:
            x['Series'] = x['DATE TIME'].diff().apply(lambda x: x.total_seconds() / 60).cumsum().fillna(0)
            x_auc = np.trapz(x['Line_Speed_Act'], x=x['Series'])
            x_time_taken = x_auc / x['Target Speed'].min()
            x_time_loss = round(x['Series'].max() - x_time_taken, 2)

        # --- Total setup
        total_setup_time = np.nan
        if pd.notna(static_setup) and pd.notna(ramp_up_time):
            total_setup_time = static_setup + ramp_up_time

        return time_loss, x_time_loss, total_setup_time, static_setup, ramp_up_time

    except Exception as e:
        print(f"Error in calculate_time_loss for batch {batch}: {e}")
        return np.nan, np.nan, np.nan, np.nan, np.nan


# ======================================================================
# == SHIFT & PRODUCTION DATE ATTRIBUTION
# ======================================================================

def calculate_shift_and_date(dt):
    if pd.isna(dt):
        return None, None
    
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    # Shift classification is based on the stored wall-clock time.
    # Avoid re-interpreting an already-stored timestamp through UTC/localtime,
    # otherwise a 06:17 record can be pushed into the wrong shift bucket.
    local_dt = dt.replace(tzinfo=None) if getattr(dt, 'tzinfo', None) is not None else dt
    time_hour = local_dt.hour
    
    if 7 <= time_hour < 15:
        shift = 'A'
        prod_date = local_dt.date()
    elif 15 <= time_hour < 23:
        shift = 'B'
        prod_date = local_dt.date()
    else:
        shift = 'C'
        if 0 <= time_hour < 7:
            prod_date = (local_dt - datetime.timedelta(days=1)).date()
        else:
            prod_date = local_dt.date()
    return shift, prod_date


# ======================================================================
# == CELERY TASK: process_changeover_data
# ======================================================================

@shared_task(name="process_changeover_data")
def process_changeover_data(dev_mode=False):
    """
    Celery task to replicate the legacy script's production analysis.
    """
    print(f"Celery Task: process_changeover_data started at {timezone.now()}...")

    # --- 1. Fetch master recipe data
    try:
        recipe_masters = RecipeMaster.objects.all()
        target_map = {_normalize_recipe_code(r.recipe_code): r.target_speed for r in recipe_masters}
        type_map = {_normalize_recipe_code(r.recipe_code): r.recipe_type for r in recipe_masters}
        # normalised code -> the spelling RecipeMaster holds, for display
        display_map = {_normalize_recipe_code(r.recipe_code): r.recipe_code for r in recipe_masters}
    except Exception as e:
        print(f"CRITICAL ERROR: Could not fetch RecipeMaster: {e}")
        return "Task failed: Could not load recipe data."

    # --- 2. Fetch raw unprocessed data (Strict limit to last 3 days)
    three_days_ago = timezone.now() - timedelta(days=3)
    unprocessed_qs = RawLineData.objects.filter(
        processed_flag=False,
        timestamp__gte=three_days_ago,
    ).order_by('timestamp')
    
    if not unprocessed_qs.exists():
        print("No unprocessed data found in the last 3 days. Task complete.")
        return "No new data to process."

    # 🌟 NEW LOGIC: EXPLICIT GAP FETCHING & EXACT CONTEXT
    # Explicitly fetch missing intermediate recipes (like 'd') that sit natively between unprocessed islands.
    unp_ids = list(unprocessed_qs.values_list('id', flat=True))
    gap_queries = Q()
    
    for i in range(len(unp_ids) - 1):
        if unp_ids[i+1] - unp_ids[i] > 1:
            gap_queries |= Q(id__gt=unp_ids[i], id__lt=unp_ids[i+1])

    # Always fetch the IMMEDIATELY PRECEDING batch of the first unprocessed row 
    # to guarantee the incoming transition (like a->b) evaluates properly.
    min_id = unp_ids[0]
    pre_context = RawLineData.objects.filter(id__lt=min_id, processed_flag=True).order_by('-timestamp')[:500]

    # Convert and dynamically merge exact datasets in Pandas
    raw_df = pd.DataFrame.from_records(unprocessed_qs.values('id', 'timestamp', 'recipe_code', 'line_speed_actual', 'line_speed_set'))
    
    if gap_queries:
        gap_data = RawLineData.objects.filter(gap_queries).order_by('timestamp')
        if gap_data.exists():
            gap_df = pd.DataFrame.from_records(gap_data.values('id', 'timestamp', 'recipe_code', 'line_speed_actual', 'line_speed_set'))
            raw_df = pd.concat([raw_df, gap_df], ignore_index=True)
            
    if pre_context.exists():
        pre_df = pd.DataFrame.from_records(pre_context.values('id', 'timestamp', 'recipe_code', 'line_speed_actual', 'line_speed_set'))
        raw_df = pd.concat([raw_df, pre_df], ignore_index=True)
        
    raw_df = raw_df.sort_values('id').reset_index(drop=True)

    raw_df.rename(columns={
        'timestamp': 'DATE TIME',
        'recipe_code': 'RECIPE',
        'line_speed_actual': 'Line_Speed_Act',
        'line_speed_set': 'Line_Speed_Set'
    }, inplace=True)

    # Make timezone-aware (local)
    raw_df['DATE TIME'] = pd.to_datetime(raw_df['DATE TIME']).dt.tz_localize(None).dt.tz_localize(LOCAL_TZ)

    print(f"Fetched {len(raw_df)} raw data records for processing.")

    # --- 3. Data preparation ---
    # Processing uses recipe_code directly; sap_code is display-only in Recipe Master.
    raw_df['RECIPE'] = raw_df['RECIPE'].apply(_normalize_recipe_code)
    raw_df['Target Speed'] = raw_df['RECIPE'].map(target_map)

    # Flag missing target speeds so the frontend API receives them, but DO NOT block!
    missing_mask = (raw_df['RECIPE'] != LINE_PASS_CODE) & (raw_df['Target Speed'].isna())
    if missing_mask.any():
        # 1. Update status to trigger API warnings, but explicitly leave processed_flag=False!
        missing_ids = raw_df.loc[missing_mask, 'id'].tolist()
        if not dev_mode:
            RawLineData.objects.filter(id__in=missing_ids).update(status="SKIPPED_NO_TARGET_SPEED")

    # Filter out 'LINE PASS'
    roll_nolp = raw_df[raw_df['RECIPE'] != LINE_PASS_CODE].copy().reset_index(drop=True)
    if roll_nolp.empty:
        print("No data left after filtering 'LINE PASS'. Marking as processed.")
        with transaction.atomic():
            unprocessed_qs.update(processed_flag=True)
        return "No data to process after filtering."

    # Batching by recipe changes
    roll_nolp['temp_batch'] = (roll_nolp['RECIPE'].ne(roll_nolp['RECIPE'].shift(1))).cumsum()
    batch_map = roll_nolp.groupby('temp_batch')['id'].min().to_dict()
    roll_nolp['BATCH'] = roll_nolp['temp_batch'].map(batch_map)

    # Merge BATCH back into raw_df (to mark processed later)
    raw_df = pd.merge(raw_df, roll_nolp[['id', 'BATCH']], on='id', how='left')
    raw_df['BATCH'] = raw_df['BATCH'].ffill()

    # Flags: first/last row of batch
    roll_batch_agg = roll_nolp.groupby('BATCH')['DATE TIME'].agg(First_Tyre='first', Last_Tyre='last').reset_index()
    roll_nolp = pd.merge(roll_nolp, roll_batch_agg, how='left', on='BATCH')
    roll_nolp['First Flag'] = np.where((roll_nolp['DATE TIME'] == roll_nolp['First_Tyre']), 1, 0)
    roll_nolp['Last Flag'] = np.where((roll_nolp['DATE TIME'] == roll_nolp['Last_Tyre']), 1, 0)
    roll_nolp.drop(['First_Tyre', 'Last_Tyre'], axis=1, inplace=True)

    # === MODIFIED: Industry standard logic applied ===
    # Ramp Down and Setup Complete now tolerate natural sensor fluctuations by using a 90% threshold.
    roll_nolp['EQUAL SPEEDS'] = np.where(
        (roll_nolp['Line_Speed_Act'] >= (roll_nolp['Target Speed'] * 0.90)) &
        (roll_nolp['Line_Speed_Act'] > 0),
        1, 0
    )

    # Other speed flags
    roll_nolp['LS DROP'] = np.where(roll_nolp['Line_Speed_Act'] == 0, 1, 0)
    roll_nolp['LS>=10'] = np.where(roll_nolp['Line_Speed_Act'] >= 10, 1, 0)
    roll_nolp['LS>=4'] = np.where(roll_nolp['Line_Speed_Act'] >= 4, 1, 0)

    # --- 4. Calculating points per batch ---
    print("Calculating changeover points (A, B, C, D) for each batch...")
    batch_dfs = []

    for batch in tqdm(roll_nolp['BATCH'].unique(), desc='Finding Points'):
        roll_batch = roll_nolp[roll_nolp['BATCH'] == batch].copy()
        roll_batch = roll_batch.sort_values(by=['DATE TIME']).reset_index(drop=True)

        # Only proceed when equal-speed periods exist
        if roll_batch['EQUAL SPEEDS'].sum() > 0:
            np1start = roll_batch.loc[roll_batch['EQUAL SPEEDS'] == 1, :].index.min()
            p2 = roll_batch.loc[roll_batch['EQUAL SPEEDS'] == 1, :].index.max()

            # first and last indices inside this roll_batch (should exist)
            try:
                first = int(roll_batch[roll_batch['First Flag'] == 1].index[0])
                last = int(roll_batch[roll_batch['Last Flag'] == 1].index[0])
            except Exception:
                # If flags are missing, skip batch
                batch_dfs.append(roll_batch)
                continue

            df = roll_batch.loc[p2:last, :]
            if len(df[df['LS DROP'] == 1].index) > 0:
                p3 = B_point(p2=p2, last=last, roll_batch=roll_batch)
                if pd.notna(p3) and int(p3) >= 0:
                    roll_batch.loc[int(p3), 'SETUP START'] = 1

            # mark ramp down at p2 (ensure int index)
            roll_batch.loc[int(p2), 'RAMP DOWN'] = 1

            # find p1 (SETUP COMPLETE): At least 7 out of 10 EQUAL_SPEEDS logic
            p1 = np.nan
            for point in range(int(np1start), int(p2)):
                np1end = point + 9
                if np1end > p2:
                    break
                
                window = roll_batch.loc[point:np1end]
                if window['EQUAL SPEEDS'].sum() >= 7:
                    # Mark the first valid point in this 10-tick window as SETUP COMPLETE
                    first_valid_idx = window[window['EQUAL SPEEDS'] == 1].index.min()
                    roll_batch.loc[first_valid_idx, 'SETUP COMPLETE'] = 1
                    p1 = first_valid_idx
                    break

            if pd.notna(p1):
                df2 = roll_batch.loc[first:int(p1), :]
                if len(df2[df2['LS DROP'] == 1]) > 0:
                    p4 = C_point(first=first, p1=int(p1), roll_batch=roll_batch)
                    if pd.notna(p4):
                        roll_batch.loc[int(p4), 'RAMP UP'] = 1

        batch_dfs.append(roll_batch)

    if not batch_dfs:
        print("No batches were processed. Exiting.")
        return "No batches found to process."

    # Combine batches
    roll2 = pd.concat(batch_dfs)
    roll2 = roll2.sort_values(by=['DATE TIME']).reset_index(drop=True)
    roll2 = roll2[~roll2['DATE TIME'].isna()]

    # Ensure marker columns exist to avoid KeyError if no batches found them
    for col in ['RAMP DOWN', 'SETUP START', 'RAMP UP', 'SETUP COMPLETE']:
        if col not in roll2.columns:
            roll2[col] = np.nan

    # --- 5. Assign previous recipe & collate timestamps ---
    print("Assigning previous recipe and collating timestamps...")
    batch_first = roll2[roll2['First Flag'] == 1].copy()
    batch_last = roll2[roll2['Last Flag'] == 1].copy()
    batch_first.rename(columns={'RECIPE': 'CURRENT_RECIPE'}, inplace=True)
    # RENAME `RECIPE` in batch_last as well
    batch_last.rename(columns={'RECIPE': 'CURRENT_RECIPE'}, inplace=True)

    unique_batches = np.sort(batch_first['BATCH'].unique())
    next_tyre = pd.DataFrame(index=unique_batches, columns=['PREVIOUS_RECIPE'], dtype='object')
    for i, b in enumerate(unique_batches):
        if i == 0:
            next_tyre.loc[b, 'PREVIOUS_RECIPE'] = np.nan
        else:
            prev_batch = unique_batches[i - 1]
            if prev_batch in batch_last['BATCH'].values:
                # Use .loc for robust lookup
                prev_recipe_series = batch_last.loc[batch_last['BATCH'] == prev_batch, 'CURRENT_RECIPE']
                if not prev_recipe_series.empty:
                    next_tyre.loc[b, 'PREVIOUS_RECIPE'] = prev_recipe_series.iloc[0]
                else:
                    next_tyre.loc[b, 'PREVIOUS_RECIPE'] = np.nan
            else:
                next_tyre.loc[b, 'PREVIOUS_RECIPE'] = np.nan

    next_tyre = next_tyre.reset_index(level=[0]).rename(columns={'index': 'BATCH'})
    batch_first = pd.merge(batch_first, next_tyre, how='left', on='BATCH')
    batch_first = batch_first[['BATCH', 'DATE TIME', 'CURRENT_RECIPE', 'PREVIOUS_RECIPE']].rename(
        columns={'DATE TIME': 'RECIPE CHANGE TIME'})

    # Initialize timestamp columns as timezone-aware NaT (local tz)
    local_dtype = pd.DatetimeTZDtype(tz=LOCAL_TZ)
    for col in ['RAMP_DOWN', 'SETUP_START', 'RAMP_UP', 'SETUP_COMPLETE']:
        batch_first[col] = pd.Series(pd.NaT, index=batch_first.index, dtype=local_dtype)

    unique_batches = np.sort(batch_first['BATCH'].unique())
    for i, b in tqdm(enumerate(unique_batches), total=len(unique_batches), desc='Collating Timestamps'):
        if i == 0:
            continue
        roll_current_batch = roll2[roll2['BATCH'] == b]
        prev_batch = unique_batches[i - 1]
        roll_prev_batch = roll2[roll2['BATCH'] == prev_batch]

        # ensure both batches have equal speed periods before collating
        if roll_current_batch['EQUAL SPEEDS'].sum() > 0 and roll_prev_batch['EQUAL SPEEDS'].sum() > 0:
            # Re-apply local awareness upon extraction

            # ramp_down from previous batch
            ramp_down_series = roll_prev_batch[roll_prev_batch['RAMP DOWN'] == 1]['DATE TIME']
            ramp_down = to_local_ts(ramp_down_series.iloc[0]) if not ramp_down_series.empty else pd.NaT

            # setup_start from previous batch (SETUP START belongs to previous)
            setup_start_series = roll_prev_batch[roll_prev_batch['SETUP START'] == 1]['DATE TIME']
            setup_start = to_local_ts(setup_start_series.iloc[0]) if not setup_start_series.empty else pd.NaT

            # ramp_up from current batch
            ramp_up_series = roll_current_batch[roll_current_batch['RAMP UP'] == 1]['DATE TIME']
            ramp_up = to_local_ts(ramp_up_series.iloc[0]) if not ramp_up_series.empty else pd.NaT

            # setup_complete from current batch
            setup_complete_series = roll_current_batch[roll_current_batch['SETUP COMPLETE'] == 1]['DATE TIME']
            setup_complete = to_local_ts(setup_complete_series.iloc[0]) if not setup_complete_series.empty else pd.NaT

            # Use .loc for safe assignment
            batch_first.loc[batch_first['BATCH'] == b, 'RAMP_DOWN'] = ramp_down
            batch_first.loc[batch_first['BATCH'] == b, 'SETUP_START'] = setup_start
            batch_first.loc[batch_first['BATCH'] == b, 'RAMP_UP'] = ramp_up
            batch_first.loc[batch_first['BATCH'] == b, 'SETUP_COMPLETE'] = setup_complete

    # --- 6. Generate batch-wise summary & calculate time losses ---
    print("Calculating time loss for valid changeovers...")

    # Standard time mapping from Database
    std_times = StandardTimeMaster.objects.all()
    standard_time_mapping = {s.changeover_key: s.standard_time for s in std_times}
    
    # Build base rules mapping (base_match_string -> changeover_key)
    # E.g., 'Steel to Steel' -> 'Steel to Steel'
    base_rules_qs = std_times.filter(is_custom=False)
    base_rules = {s.changeover_key: s.changeover_key for s in base_rules_qs}
    
    # Build custom rules mapping ( (from_recipe, to_recipe) -> changeover_key )
    custom_rules_qs = ChangeoverRule.objects.select_related('standard_time_master').all()
    custom_rules = {(r.from_recipe, r.to_recipe): r.standard_time_master.changeover_key for r in custom_rules_qs}

    # Filter complete changeovers
    # (Removed dropna to keep incomplete changeovers with remarks)
    batch_first_nonna = batch_first[batch_first['BATCH'] != batch_first['BATCH'].min()].copy()
    if batch_first_nonna.empty:
        print("No changeovers found in this data chunk. Data will be re-analyzed next run.")
        return "No changeovers found to summarize."

    # Generate Remarks for missing points
    def get_max_speed(b_df):
        return round(b_df['Line_Speed_Act'].max(), 1) if not b_df.empty and 'Line_Speed_Act' in b_df.columns else 0

    batch_first_nonna['Remarks'] = pd.Series([None] * len(batch_first_nonna), index=batch_first_nonna.index, dtype='object')
    all_unique_batches = np.sort(roll2['BATCH'].unique())
    
    for bat in tqdm(batch_first_nonna['BATCH'].unique(), desc='Calculating Remarks'):
        roll_current_batch = roll2[roll2['BATCH'] == bat]
        
        bat_index = np.where(all_unique_batches == bat)[0][0]
        prev_batch = all_unique_batches[bat_index - 1] if bat_index > 0 else None
        
        roll_prev_batch = roll2[roll2['BATCH'] == prev_batch] if prev_batch is not None else pd.DataFrame()
        
        row = batch_first_nonna[batch_first_nonna['BATCH'] == bat].iloc[0]
        prev_recipe = row['PREVIOUS_RECIPE'] if pd.notna(row['PREVIOUS_RECIPE']) else "Unknown"
        curr_recipe = row['CURRENT_RECIPE'] if pd.notna(row['CURRENT_RECIPE']) else "Unknown"

        remarks = []
        if roll_prev_batch.empty or 'Target Speed' not in roll_prev_batch.columns or roll_prev_batch['Target Speed'].min() == 0 or pd.isna(roll_prev_batch['Target Speed'].min()):
            remarks.append(f"target speed is missing for the previous recipe '{prev_recipe}'")
        elif roll_prev_batch['EQUAL SPEEDS'].sum() == 0:
            target = roll_prev_batch['Target Speed'].min()
            max_spd = get_max_speed(roll_prev_batch)
            remarks.append(f"the target speed for the previous recipe '{prev_recipe}' is {target} but the max speed ran was {max_spd}")
        elif roll_prev_batch['LS DROP'].sum() == 0:
            remarks.append(f"zero speed was not reached during the setup from '{prev_recipe}'")

        if roll_current_batch.empty or 'Target Speed' not in roll_current_batch.columns or roll_current_batch['Target Speed'].min() == 0 or pd.isna(roll_current_batch['Target Speed'].min()):
            remarks.append(f"target speed is missing for the current recipe '{curr_recipe}'")
        elif roll_current_batch['EQUAL SPEEDS'].sum() == 0:
            target = roll_current_batch['Target Speed'].min()
            max_spd = get_max_speed(roll_current_batch)
            remarks.append(f"the target speed for the current recipe '{curr_recipe}' is {target} but the max speed ran was {max_spd}")
        elif roll_current_batch['LS DROP'].sum() == 0:
            remarks.append(f"zero speed was not reached during the setup for '{curr_recipe}'")

        if remarks:
            remark_str = "Changeover statistics cannot be calculated because " + " and ".join(remarks) + "."
            batch_first_nonna.loc[batch_first_nonna['BATCH'] == bat, 'Remarks'] = remark_str
        else:
            if pd.isna(row['RAMP_DOWN']) or pd.isna(row['SETUP_START']) or pd.isna(row['RAMP_UP']) or pd.isna(row['SETUP_COMPLETE']):
                batch_first_nonna.loc[batch_first_nonna['BATCH'] == bat, 'Remarks'] = "Changeover statistics cannot be calculated because required setup points could not be identified."

    # Calculate losses and times
    for bat in tqdm(batch_first_nonna['BATCH'].unique(), desc='Calculating Loss'):
        # Pass the correct 'batch_first' (the one with all points) to the time loss function
        res = calculate_time_loss(bat, roll2, batch_first_nonna)
        batch_first_nonna.loc[batch_first_nonna['BATCH'] == bat, 'Ramp up time loss'] = res[0]
        batch_first_nonna.loc[batch_first_nonna['BATCH'] == bat, 'Ramp down time loss'] = res[1]
        batch_first_nonna.loc[batch_first_nonna['BATCH'] == bat, 'Setup Time'] = res[2]
        batch_first_nonna.loc[batch_first_nonna['BATCH'] == bat, 'static_setup_time'] = res[3]
        batch_first_nonna.loc[batch_first_nonna['BATCH'] == bat, 'ramp_up_time'] = res[4]

        # If Setup Time (act) was successfully calculated, the frontend stats are valid, so remove the error remark.
        if pd.notna(res[2]):
            batch_first_nonna.loc[batch_first_nonna['BATCH'] == bat, 'Remarks'] = None

    # Apply type mapping
    batch_first_nonna['Current Type'] = batch_first_nonna['CURRENT_RECIPE'].map(type_map)
    batch_first_nonna['Previous Type'] = batch_first_nonna['PREVIOUS_RECIPE'].map(type_map)

    # Changeover type logic
    batch_first_nonna['Changeover'] = pd.Series([None] * len(batch_first_nonna), index=batch_first_nonna.index, dtype='object')
    for idx, row in batch_first_nonna.iterrows():
        prev_recipe = row.get('PREVIOUS_RECIPE')
        curr_recipe = row.get('CURRENT_RECIPE')
        prev_type = row.get('Previous Type')
        curr_type = row.get('Current Type')
        
        changeover_val = resolve_changeover(prev_recipe, curr_recipe, prev_type, curr_type, custom_rules, base_rules)
        batch_first_nonna.loc[idx, 'Changeover'] = changeover_val

    # Standard time
    batch_first_nonna['Standard Time'] = batch_first_nonna['Changeover'].map(standard_time_mapping)

    batch_first_nonna['Shift'] = None
    batch_first_nonna['Production Date'] = None
    for idx, row in batch_first_nonna.iterrows():
        shift, prod_date = calculate_shift_and_date(row['RECIPE CHANGE TIME'])
        batch_first_nonna.loc[idx, 'Shift'] = shift
        batch_first_nonna.loc[idx, 'Production Date'] = prod_date

    # Replace pandas/np nulls with None for DB compatibility
    batch_first_nonna = batch_first_nonna.replace({np.nan: None, pd.NaT: None})

    print(f"Saving {len(batch_first_nonna)} summary records to database...")

    # Helper to convert pandas/numpy values to Python types for Django
    def clean_value(value):
        if pd.isna(value):
            return None
        if isinstance(value, (pd.Timestamp, np.datetime64)):
            # to_pydatetime preserves timezone if present
            return pd.to_datetime(value).to_pydatetime()
        if isinstance(value, (np.integer, np.floating)):
            try:
                # Use .item() to convert numpy types to python native types
                return value.item()
            except Exception:
                return value
        # strings, ints, None pass through
        return value

    try:
        with transaction.atomic():
            
            # Always upsert for the current computation window so corrected recipes
            # can overwrite previously incomplete/unknown summaries.
            batch_to_save = batch_first_nonna
                
            summary_objects_created = 0
            for _, row in tqdm(batch_to_save.iterrows(), total=batch_to_save.shape[0], desc='Saving to DB'):
                
                # Get or create the summary object to prevent overwriting worker comments
                summary, created = ChangeoverSummary.objects.get_or_create(
                    batch=clean_value(row['BATCH'])
                )
                
                # Update calculated fields regardless of whether it's new or existing
                summary.recipe_change_time = clean_value(row['RECIPE CHANGE TIME'])
                summary.current_recipe = _master_spelling(
                    clean_value(row['CURRENT_RECIPE']), display_map)
                summary.previous_recipe = _master_spelling(
                    clean_value(row['PREVIOUS_RECIPE']), display_map)
                summary.ramp_down_timestamp = clean_value(row['RAMP_DOWN'])
                summary.setup_start_timestamp = clean_value(row['SETUP_START'])
                summary.ramp_up_timestamp = clean_value(row['RAMP_UP'])
                summary.setup_complete_timestamp = clean_value(row['SETUP_COMPLETE'])
                summary.ramp_up_time_loss = clean_value(row.get('Ramp up time loss'))
                summary.ramp_down_time_loss = clean_value(row.get('Ramp down time loss'))
                summary.setup_time_actual = clean_value(row.get('Setup Time'))
                summary.standard_time = clean_value(row.get('Standard Time'))
                summary.static_setup_time = clean_value(row.get('static_setup_time'))
                summary.ramp_up_time = clean_value(row.get('ramp_up_time'))
                summary.current_type = clean_value(row.get('Current Type'))
                summary.previous_type = clean_value(row.get('Previous Type'))
                summary.change_over_type = clean_value(row.get('Changeover'))
                summary.remarks = clean_value(row.get('Remarks'))
                summary.shift = clean_value(row.get('Shift'))
                summary.production_date = clean_value(row.get('Production Date'))
                summary.save()
                
                summary_objects_created += 1

            # <--- MODIFIED LOGIC: STRICT DUAL-VERIFICATION (IN & OUT) ---
            summary_objects_created = len(batch_first_nonna)
            
            line_pass_ids = []
            success_ids = []
            processed_ids = []

            if not raw_df.empty:
                processed_ids = raw_df['id'].tolist()
                
                # Fetch all unique batches physically present in this dataset run
                all_batches = raw_df['BATCH'].dropna().unique().tolist()
                
                # We natively sweep missing recipes into SKIPPED without marking them True
                missing_target_ids = raw_df[
                    (raw_df['RECIPE'] != LINE_PASS_CODE) & 
                    (raw_df['Target Speed'].isna())
                ]['id'].tolist()

                for bat in all_batches:
                    bat_df_ids = raw_df[raw_df['BATCH'] == bat]['id'].tolist()
                    
                    # 1. INCOMING transition success? (Is there a summary anchored on THIS batch?)
                    in_succeeded = ChangeoverSummary.objects.filter(batch=clean_value(bat)).exists()
                    
                    # Bypass incoming check for the very first batch in history (Big Bang / pruned data scenario)
                    if not in_succeeded:
                        if not RawLineData.objects.filter(id__lt=bat_df_ids[0]).exists():
                            in_succeeded = True
                    
                    # 2. OUTGOING transition success? (Is there a summary anchored on the NEXT batch?)
                    next_batches = raw_df[raw_df['BATCH'] > bat]['BATCH']
                    if not next_batches.empty:
                        next_bat = next_batches.min()
                        out_succeeded = ChangeoverSummary.objects.filter(batch=clean_value(next_bat)).exists()
                    else:
                        out_succeeded = False # End of queue, hasn't physically occurred yet
                    
                    # EXPLICIT RULE: Only flag as processed if BOTH incoming and outgoing transitions perfectly succeeded.
                    if in_succeeded and out_succeeded:
                        success_ids.extend(bat_df_ids)

                # Safely segregate line passes
                line_pass_ids = raw_df[raw_df['RECIPE'] == LINE_PASS_CODE]['id'].tolist()

                # Filter success_ids to ensure no line passes or missing targets mistakenly got in
                success_ids = [i for i in success_ids if i not in line_pass_ids and i not in missing_target_ids]
                
            if not dev_mode:
                if success_ids:
                    RawLineData.objects.filter(id__in=success_ids).update(
                        processed_flag=True, status="SUCCESS"
                    )
                if line_pass_ids:
                    RawLineData.objects.filter(id__in=line_pass_ids).update(
                        processed_flag=True, status="IGNORED_LINE_PASS"
                    )
                if missing_target_ids:
                    RawLineData.objects.filter(id__in=missing_target_ids).update(
                        processed_flag=True, status="SKIPPED_NO_TARGET_SPEED"
                    )

                # CLEAR INVALID HISTORY: Anything that isn't the currently running batch 
                # and failed all success checks is mathematically invalid and should be purged.
                all_batches = raw_df['BATCH'].dropna().unique().tolist()
                last_bat = max(all_batches) if all_batches else None
                
                if last_bat:
                    historical_ids = raw_df[raw_df['BATCH'] < last_bat]['id'].tolist()
                    failed_mechanics = [
                        i for i in historical_ids 
                        if i not in success_ids 
                        and i not in line_pass_ids 
                        and i not in missing_target_ids
                    ]
                    if failed_mechanics:
                        RawLineData.objects.filter(id__in=failed_mechanics).update(
                            processed_flag=True, status="FAILED_OPERATIONAL_SPEED"
                        )

            print(f"Successfully created/updated {summary_objects_created} summary records.")
            print(f"Purged {len(success_ids)} SUCCESS records.")
            print(f"Purged {len(line_pass_ids)} LINE PASS records.")
            print(f"Flagged {len(missing_target_ids)} missing-target records (kept unprocessed).")

            return f"Task success: Processed {len(raw_df)} records, saved {summary_objects_created} summaries, marked {len(processed_ids)} as complete."

    except Exception as e:
        print(f"CRITICAL ERROR during database transaction: {e}")
        raise e


# ======================================================================
# == CELERY TASK: sync_recipe_master_from_bom
# ======================================================================

@shared_task(name="sync_recipe_master_from_bom")
def sync_recipe_master_from_bom():
    """
    Celery task to sync RecipeMaster from MaterialMaster (BOM table).
    This task fetches all records from the MaterialMaster table and ensures 
    that they exist in RecipeMaster. It updates sap_code if missing.
    """
    print(f"Celery Task: sync_recipe_master_from_bom started at {timezone.now()}...")
    try:
        # Fetch only 4RC recipes from the bom table
        bom_recipes = MaterialMaster.objects.filter(equipment='4RC')
        
        created_count = 0
        updated_count = 0

        for bom in bom_recipes:
            recipe_code = bom.recipe_code
            sap_code = bom.sap_code
            
            if not recipe_code:
                continue
                
            recipe_code = str(recipe_code).strip()
            sap_code = str(sap_code).strip() if sap_code else None

            # Match on the normalised code so a BOM row spelled 'CP034HI' does
            # not create a second master row beside an existing 'CP 034 HI'.
            # A new row keeps the ERP's spelling, which becomes the spelling
            # shown everywhere downstream.
            obj, created = upsert_master_recipe(
                recipe_code,
                defaults={
                    'sap_code': sap_code,
                    'target_speed': 0.0, # Default, must be updated via API later
                    'recipe_type': 'unknown' # Default, must be updated via API later
                },
                update_existing=False,
            )
            if obj is None:
                continue

            if created:
                created_count += 1
            else:
                # If it already exists, update sap_code if it's different/empty
                if obj.sap_code != sap_code:
                    obj.sap_code = sap_code
                    obj.save(update_fields=['sap_code'])
                    updated_count += 1

        print(f"Sync complete. Created: {created_count}, Updated sap_code: {updated_count}.")
        
        return f"Success: Created {created_count}, Updated {updated_count}"

    except Exception as e:
        error_msg = f"CRITICAL ERROR syncing from MaterialMaster: {e}"
        print(error_msg)
        return error_msg


