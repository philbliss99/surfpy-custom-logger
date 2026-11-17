import argparse
import pandas as pd
import numpy as np
from pathlib import Path

def load_and_backfill(path):
    df = pd.read_csv(path, parse_dates=['issued_at_utc','target_time_utc'])
    
    # Backfill: find actual at target_time from lead=0 rows
    if 'actual_hs_ft' not in df.columns:
        raise ValueError(f"No actual_hs_ft in {path}")
        
    lead0 = df[df['lead_hours']==0][['target_time_utc','actual_hs_ft','actual_period_s']].rename(
        columns={'actual_hs_ft':'actual_at_target_ft','actual_period_s':'actual_period_at_target'}
    ).dropna(subset=['actual_at_target_ft'])

    df_merged = pd.merge(df, lead0, on='target_time_utc', how='left')
    df_merged['actual_final_ft'] = df_merged['actual_hs_ft'].combine_first(df_merged['actual_at_target_ft'])
    df_merged['actual_final_period'] = df_merged['actual_period_s'].combine_first(df_merged['actual_period_at_target'])
    
    df_merged['error_ft'] = df_merged['actual_final_ft'] - df_merged['forecast_hs_ft']
    df_merged['ratio'] = df_merged['actual_final_ft'] / df_merged['forecast_hs_ft']
    df_merged['source_file'] = Path(path).name
    
    # Add buoy metadata if present
    if 'buoy_name' in df.columns:
        df_merged['spot'] = df_merged['buoy_name']
    elif 'buoy_id' in df.columns:
        df_merged['spot'] = df_merged['buoy_id'].astype(str)
    else:
        df_merged['spot'] = Path(path).stem
        
    return df_merged

def analyze_one(df_valid, spot_name):
    print(f"\n{'='*60}")
    print(f"SPOT: {spot_name} - {len(df_valid)} paired rows")
    print(f"{'='*60}")
    
    if len(df_valid) == 0:
        print("No paired data yet - logger hasn't backfilled")
        return None, None

    print("\n--- PHASE 1: Bias Baseline (error = actual - forecast) ---")
    print(f"Mean error: {df_valid['error_ft'].mean():+.3f} ft  (negative = forecast overpredicts)")
    print(f"Median error: {df_valid['error_ft'].median():+.3f} ft")
    print(f"MAE raw: {df_valid['error_ft'].abs().mean():.3f} ft")
    print(f"Mean ratio actual/forecast: {df_valid['ratio'].mean():.3f}")

    print("\nBias by lead_hours:")
    bias_lead = df_valid.groupby('lead_hours')['error_ft'].agg(['mean','median','count','std']).round(3)
    print(bias_lead)
    
    print("\nBias by forecast period:")
    df_valid['period_bin'] = pd.cut(df_valid['forecast_period_s'], bins=[0,5,7,10,20], labels=['<5s wind','5-7s mixed','7-10s swell','>10s groundswell'])
    print(df_valid.groupby('period_bin', observed=True)['error_ft'].agg(['mean','count']).round(3))

    print("\nBias by forecast size:")
    df_valid['size_bin'] = pd.cut(df_valid['forecast_hs_ft'], bins=[0,2,4,6,20], labels=['<2ft','2-4ft','4-6ft','>6ft'])
    print(df_valid.groupby('size_bin', observed=True)['error_ft'].agg(['mean','count']).round(3))

    bias_by_lead = df_valid.groupby('lead_hours')['error_ft'].mean().to_dict()
    
    # Phase 2 eval
    df_valid = df_valid.sort_values('target_time_utc')
    if len(df_valid) >= 10:
        split_idx = int(len(df_valid)*0.7)
        train = df_valid.iloc[:split_idx]
        test = df_valid.iloc[split_idx:]
        
        test = test.copy()
        test['bias_correction'] = test['lead_hours'].map(bias_by_lead)
        test['corrected_bias_ft'] = test['forecast_hs_ft'] + test['bias_correction']
        
        from sklearn.metrics import mean_absolute_error
        mae_raw = mean_absolute_error(test['actual_final_ft'], test['forecast_hs_ft'])
        mae_bias = mean_absolute_error(test['actual_final_ft'], test['corrected_bias_ft'])
        
        print(f"\n--- PHASE 1 Evaluation (time-split last 30%) ---")
        print(f"Raw MAE: {mae_raw:.3f} ft")
        print(f"Bias-corrected MAE: {mae_bias:.3f} ft -> improvement {mae_raw-mae_bias:+.3f} ft ({(1-mae_bias/mae_raw)*100:+.1f}%)")
        
        # Try XGBoost if enough data
        try:
            import xgboost as XGB
            if len(train) >= 40:
                features = ['forecast_hs_ft','forecast_period_s','lead_hours']
                X_train = train[features].fillna(train[features].median())
                y_train = train['error_ft']
                X_test = test[features].fillna(train[features].median())
                
                model = XGB.XGBRegressor(n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42, verbosity=0)
                model.fit(X_train, y_train)
                test['predicted_error'] = model.predict(X_test)
                test['corrected_xgb_ft'] = test['forecast_hs_ft'] + test['predicted_error']
                mae_xgb = mean_absolute_error(test['actual_final_ft'], test['corrected_xgb_ft'])
                print(f"XGBoost MAE: {mae_xgb:.3f} ft -> improvement {mae_raw-mae_xgb:+.3f} ft")
                print(f"Feature importance: {dict(zip(features, model.feature_importances_.round(3)))}")
            else:
                print(f"Need 40+ rows for XGBoost, have {len(train)} train rows - collect more data")
        except ImportError:
            print("Install xgboost for Phase 2: pip install xgboost")
    
    return bias_by_lead, df_valid

def main():
    parser = argparse.ArgumentParser(description='Analyze surf forecast error = actual - forecast')
    parser.add_argument('--csv', type=str, default=None, help='Path to CSV file or folder')
    parser.add_argument('--all', action='store_true', help='Analyze all CSVs in folder')
    args = parser.parse_args()

    csv_path = Path(args.csv) if args.csv else None
    
    files = []
    if csv_path and csv_path.is_dir():
        files = list(csv_path.glob('*.csv'))
    elif csv_path and csv_path.is_file():
        files = [csv_path]
    elif args.all:
        # look in common locations
        for p in [Path('.'), Path('./data'), Path('../data')]:
            if p.exists():
                files.extend(list(p.glob('*.csv')))
    else:
        # auto-detect in cwd
        files = list(Path('.').glob('*log*.csv')) + list(Path('./data').glob('*.csv')) if Path('./data').exists() else list(Path('.').glob('*.csv'))
    
    if not files:
        print("No CSVs found. Use --csv path/to/file.csv or --csv path/to/folder")
        return
    
    print(f"Found {len(files)} file(s): {[f.name for f in files]}")
    
    all_valid = []
    for f in files:
        try:
            df_all = load_and_backfill(f)
            df_valid = df_all.dropna(subset=['actual_final_ft','forecast_hs_ft']).copy()
            bias, valid = analyze_one(df_valid, f.stem)
            if valid is not None:
                all_valid.append(valid)
        except Exception as e:
            print(f"\nFailed to analyze {f}: {e}")

    # Combined analysis if multiple spots
    if len(all_valid) > 1:
        combined = pd.concat(all_valid, ignore_index=True)
        print(f"\n{'='*60}")
        print(f"COMBINED ACROSS {len(all_valid)} SPOTS - {len(combined)} rows")
        print(f"{'='*60}")
        print(combined.groupby('spot')['error_ft'].agg(['mean','count','std']).round(3))
        print("\nThis shows which buoys forecast model over/under predicts at")
        print("Use spot as a feature for a global model: final = forecast + model(lead, period, spot)")

if __name__ == "__main__":
    main()
