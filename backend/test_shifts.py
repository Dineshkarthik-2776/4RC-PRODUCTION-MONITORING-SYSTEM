import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

import pandas as pd
from data_processor.models import RawLineData
from data_processor.tasks import LOCAL_TZ
import datetime

def calculate_shift_and_date(dt):
    if pd.isna(dt):
        return None, None
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
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

unprocessed_qs = RawLineData.objects.order_by('-timestamp')[:5000]
raw_df = pd.DataFrame.from_records(unprocessed_qs.values('timestamp', 'recipe_code'))
raw_df.rename(columns={'timestamp': 'DATE TIME', 'recipe_code': 'RECIPE'}, inplace=True)
raw_df['DATE TIME'] = pd.to_datetime(raw_df['DATE TIME']).dt.tz_localize(None).dt.tz_localize(LOCAL_TZ)
raw_df = raw_df.sort_values(by='DATE TIME').reset_index(drop=True)

raw_df['PREVIOUS_RECIPE'] = raw_df['RECIPE'].shift(1)
raw_df['Recipe_Changed'] = (raw_df['RECIPE'] != raw_df['PREVIOUS_RECIPE']) & (raw_df['PREVIOUS_RECIPE'].notna())
raw_df['BATCH'] = raw_df['Recipe_Changed'].cumsum()

roll_nolp = raw_df[raw_df['RECIPE'] != '2_LINE_PASS'].copy().reset_index(drop=True)
roll_nolp['First Flag'] = (~roll_nolp['BATCH'].duplicated(keep='first')).astype(int)

batch_first = roll_nolp[roll_nolp['First Flag'] == 1].copy()
batch_first = batch_first[['BATCH', 'DATE TIME', 'RECIPE', 'PREVIOUS_RECIPE']].rename(columns={'DATE TIME': 'RECIPE CHANGE TIME'})

print('--- DRY RUN: RECENT BATCHES ---')
for idx, row in batch_first.dropna(subset=['PREVIOUS_RECIPE']).tail(5).iterrows():
    print(f"Change Time: {row['RECIPE CHANGE TIME']} --- Shift: {calculate_shift_and_date(row['RECIPE CHANGE TIME'])[0]}")
