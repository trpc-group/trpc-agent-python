#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Generate a comprehensive data summary report for a CSV file.
"""
import sys

import numpy as np
import pandas as pd


def summarize_data(input_file: str, output_file: str):
    """
    Generate a comprehensive data summary report.

    Args:
        input_file: Path to input CSV file
        output_file: Path to output summary file
    """
    try:
        # Read CSV file
        df = pd.read_csv(input_file)

        # Create output directory if needed
        import os
        os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)

        # Generate comprehensive summary report
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write("Data Summary Report\n")
            f.write("=" * 70 + "\n\n")

            # Basic Information
            f.write("1. DATASET OVERVIEW\n")
            f.write("-" * 70 + "\n")
            f.write(f"   Input File:     {input_file}\n")
            f.write(f"   Total Rows:     {df.shape[0]:,}\n")
            f.write(f"   Total Columns:  {df.shape[1]}\n")
            f.write(f"   Memory Usage:   {df.memory_usage(deep=True).sum() / 1024:.2f} KB\n")
            f.write("\n")

            # Column Information
            f.write("2. COLUMN INFORMATION\n")
            f.write("-" * 70 + "\n")
            for i, col in enumerate(df.columns, 1):
                dtype = df[col].dtype
                non_null = df[col].notna().sum()
                null_count = df[col].isna().sum()
                null_pct = (null_count / len(df)) * 100 if len(df) > 0 else 0

                f.write(f"   {i}. {col}\n")
                f.write(f"      Type:        {dtype}\n")
                f.write(f"      Non-null:    {non_null:,} ({100-null_pct:.1f}%)\n")
                if null_count > 0:
                    f.write(f"      Null:        {null_count:,} ({null_pct:.1f}%)\n")

                # Column-specific statistics
                if pd.api.types.is_numeric_dtype(df[col]):
                    series = df[col].dropna()
                    if len(series) > 0:
                        f.write(f"      Mean:        {series.mean():.4f}\n")
                        f.write(f"      Std:         {series.std():.4f}\n")
                        f.write(f"      Min:         {series.min():.4f}\n")
                        f.write(f"      Max:         {series.max():.4f}\n")
                elif pd.api.types.is_object_dtype(df[col]):
                    unique_count = df[col].nunique()
                    f.write(f"      Unique:      {unique_count:,}\n")
                    if unique_count <= 10:
                        top_values = df[col].value_counts().head(5)
                        f.write(f"      Top values:  {dict(top_values)}\n")

                f.write("\n")

            # Data Quality
            f.write("3. DATA QUALITY ASSESSMENT\n")
            f.write("-" * 70 + "\n")
            total_cells = df.shape[0] * df.shape[1]
            missing_cells = df.isnull().sum().sum()
            completeness = ((total_cells - missing_cells) / total_cells * 100) if total_cells > 0 else 0

            f.write(f"   Overall Completeness: {completeness:.2f}%\n")
            f.write(f"   Missing Values:       {missing_cells:,} out of {total_cells:,} cells\n")

            if missing_cells > 0:
                f.write("\n   Missing Values by Column:\n")
                missing_by_col = df.isnull().sum()
                missing_by_col = missing_by_col[missing_by_col > 0].sort_values(ascending=False)
                for col, count in missing_by_col.items():
                    pct = (count / len(df)) * 100
                    f.write(f"      {col}: {count:,} ({pct:.1f}%)\n")
            else:
                f.write("   ✓ No missing values found.\n")
            f.write("\n")

            # Duplicate Rows
            duplicates = df.duplicated().sum()
            f.write(f"   Duplicate Rows:      {duplicates:,}")
            if duplicates > 0:
                pct = (duplicates / len(df)) * 100
                f.write(f" ({pct:.1f}%)\n")
            else:
                f.write(" ✓ No duplicates found.\n")
            f.write("\n")

            # Statistical Summary
            numeric_cols = df.select_dtypes(include=['number']).columns
            if len(numeric_cols) > 0:
                f.write("4. STATISTICAL SUMMARY (Numeric Columns)\n")
                f.write("-" * 70 + "\n")
                f.write(str(df[numeric_cols].describe()))
                f.write("\n\n")

                # Correlation insights
                if len(numeric_cols) > 1:
                    f.write("5. CORRELATION INSIGHTS\n")
                    f.write("-" * 70 + "\n")
                    corr_matrix = df[numeric_cols].corr()
                    # Find strong correlations (|r| > 0.7)
                    strong_corr = []
                    for i in range(len(corr_matrix.columns)):
                        for j in range(i + 1, len(corr_matrix.columns)):
                            corr_val = corr_matrix.iloc[i, j]
                            if abs(corr_val) > 0.7:
                                strong_corr.append((corr_matrix.columns[i], corr_matrix.columns[j], corr_val))

                    if strong_corr:
                        f.write("   Strong correlations (|r| > 0.7):\n")
                        for col1, col2, corr_val in strong_corr:
                            f.write(f"      {col1} ↔ {col2}: {corr_val:.3f}\n")
                    else:
                        f.write("   No strong correlations found (|r| > 0.7).\n")
                    f.write("\n")

            # Sample Data Preview
            f.write("6. DATA PREVIEW\n")
            f.write("-" * 70 + "\n")
            f.write("   First 5 rows:\n")
            f.write(str(df.head()))
            f.write("\n\n")

            f.write("   Last 5 rows:\n")
            f.write(str(df.tail()))
            f.write("\n\n")

            f.write("=" * 70 + "\n")
            f.write("Summary report generated successfully.\n")
            f.write("=" * 70 + "\n")

        print(f"Summary report written to: {output_file}")

    except FileNotFoundError:
        print(f"Error: Input file not found: {input_file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error generating summary: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: summarize_data.py <input_csv> <output_summary>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    summarize_data(input_file, output_file)
