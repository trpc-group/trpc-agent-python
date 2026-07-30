#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Calculate descriptive statistics for numeric columns in a CSV file.
"""
import sys

import numpy as np
import pandas as pd


def describe_data(input_file: str, output_file: str):
    """
    Calculate descriptive statistics for numeric columns.

    Args:
        input_file: Path to input CSV file
        output_file: Path to output statistics file
    """
    try:
        # Read CSV file
        df = pd.read_csv(input_file)

        # Create output directory if needed
        import os
        os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)

        # Get numeric columns
        numeric_cols = df.select_dtypes(include=['number']).columns

        # Generate statistics report
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write("Descriptive Statistics Report\n")
            f.write("=" * 60 + "\n\n")

            f.write(f"Input File: {input_file}\n")
            f.write(f"Total Rows: {len(df)}\n")
            f.write(f"Numeric Columns: {len(numeric_cols)}\n\n")

            if len(numeric_cols) == 0:
                f.write("No numeric columns found in the dataset.\n")
            else:
                f.write("Statistics by Column:\n")
                f.write("-" * 60 + "\n")

                for col in numeric_cols:
                    f.write(f"\nColumn: {col}\n")
                    f.write("-" * 40 + "\n")

                    series = df[col].dropna()
                    if len(series) > 0:
                        f.write(f"  Count:        {len(series)}\n")
                        f.write(f"  Mean:         {series.mean():.4f}\n")
                        f.write(f"  Median:       {series.median():.4f}\n")
                        f.write(f"  Std Dev:      {series.std():.4f}\n")
                        f.write(f"  Min:          {series.min():.4f}\n")
                        f.write(f"  Max:          {series.max():.4f}\n")
                        f.write(f"  25th Percentile: {series.quantile(0.25):.4f}\n")
                        f.write(f"  75th Percentile: {series.quantile(0.75):.4f}\n")
                        f.write(f"  Range:        {series.max() - series.min():.4f}\n")

                        # Additional statistics
                        if len(series) > 1:
                            f.write(f"  Variance:     {series.var():.4f}\n")
                            f.write(f"  Skewness:     {series.skew():.4f}\n")
                            f.write(f"  Kurtosis:     {series.kurtosis():.4f}\n")

                        # Missing values
                        missing = df[col].isnull().sum()
                        if missing > 0:
                            f.write(f"  Missing:      {missing} ({missing/len(df)*100:.1f}%)\n")
                    else:
                        f.write("  All values are missing.\n")

                # Correlation matrix if multiple numeric columns
                if len(numeric_cols) > 1:
                    f.write("\n" + "=" * 60 + "\n")
                    f.write("Correlation Matrix:\n")
                    f.write("-" * 60 + "\n")
                    corr_matrix = df[numeric_cols].corr()
                    f.write(corr_matrix.to_string())
                    f.write("\n")

            f.write("\n" + "=" * 60 + "\n")
            f.write("Statistics report generated successfully.\n")

        print(f"Statistics report written to: {output_file}")

    except FileNotFoundError:
        print(f"Error: Input file not found: {input_file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error calculating statistics: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: describe_data.py <input_csv> <output_stats>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    describe_data(input_file, output_file)
