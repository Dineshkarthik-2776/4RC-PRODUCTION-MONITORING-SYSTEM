import os
import django
from django.db import connection

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

with connection.cursor() as cursor:
    # 1. Drop the orphan table that causes "Table already exists" if we don't
    try:
        print("Dropping changeovercorrectionrequest...")
        cursor.execute("DROP TABLE IF EXISTS changeovercorrectionrequest")
    except Exception as e:
        print(f"Error dropping changeovercorrectionrequest: {e}")

    # 2. Rename tbl_summary to ChangeoverSummary
    try:
        # Check if tbl_summary exists
        cursor.execute("SHOW TABLES LIKE 'tbl_summary'")
        if cursor.fetchone():
            print("Renaming tbl_summary to ChangeoverSummary...")
            cursor.execute("RENAME TABLE tbl_summary TO ChangeoverSummary")
        else:
            print("tbl_summary not found. Checking if ChangeoverSummary already exists...")
            cursor.execute("SHOW TABLES LIKE 'ChangeoverSummary'")
            if cursor.fetchone():
                print("ChangeoverSummary already exists.")
            else:
                print("CRITICAL: Neither tbl_summary nor ChangeoverSummary found!")
    except Exception as e:
        print(f"Error renaming tbl_summary: {e}")

    # 3. Check FRC/4rc situation (Optional but good for consistency)
    try:
        cursor.execute("SHOW TABLES LIKE '4rc'")
        if cursor.fetchone():
            print("Found table '4rc'. Checking if 'FRC' exists...")
            cursor.execute("SHOW TABLES LIKE 'FRC'")
            if not cursor.fetchone():
                 # Only rename if FRC doesn't exist
                 print("Renaming 4rc to FRC...")
                 cursor.execute("RENAME TABLE `4rc` TO FRC") 
            else:
                 print("Both 4rc and FRC exist. Leaving them alone.")
    except Exception as e:
        print(f"Error checking/renaming 4rc: {e}")

print("Database fix script completed.")
