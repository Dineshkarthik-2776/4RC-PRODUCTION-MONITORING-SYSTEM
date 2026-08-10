import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()
import pandas as pd
from data_processor.models import RawLineData, RecipeMaster

unprocessed_data = RawLineData.objects.filter(processed_flag=False).order_by('timestamp')
print("Unprocessed records:", unprocessed_data.count())
if unprocessed_data.exists():
    raw_df = pd.DataFrame.from_records(unprocessed_data.values('id', 'timestamp', 'recipe_code', 'line_speed_actual', 'line_speed_set'))
    print("Recipes in chunk:", raw_df['recipe_code'].unique())
    
    recipe_masters = RecipeMaster.objects.all()
    target_map = {r.recipe_code: r.target_speed for r in recipe_masters}
    print("Target map:", list(target_map.items())[:10])
    
    SAP_recipe_mapping = {'CWBHT43BELT1': 'CP200', 'CWCH30256': 'CP 700', 'CWBP65': 'CP 650', 'CWBP60': 'CP 500', 'CWBHT40S564': 'CP 840', 'CWBHTCP100': 'CPJ114', 'CWBHTCP51': 'CP 51', 'CAP 66': 'CAP 28', 'CPJ 114': 'CPJ114'}
    raw_df['RECIPE'] = raw_df['recipe_code'].str.strip().replace(SAP_recipe_mapping)
    raw_df['Target Speed'] = raw_df['RECIPE'].map(target_map)
    raw_df['EQUAL SPEEDS'] = ((raw_df['line_speed_actual'] == raw_df['Target Speed']) & (raw_df['line_speed_actual'] > 0)).astype(int)
    
    print("Missing targets for:", raw_df[raw_df['Target Speed'].isna()]['RECIPE'].unique())
    
    for recipe in raw_df['RECIPE'].unique():
        sub = raw_df[raw_df['RECIPE'] == recipe]
        eq_sum = sub['EQUAL SPEEDS'].sum()
        print(f"Recipe {recipe} -> Equal Speeds found: {eq_sum}")
        if eq_sum == 0:
            print(f"  WARNING: Actual max speed {sub['line_speed_actual'].max()} vs Target {sub['Target Speed'].max()}")
