#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Analyze a CSV file and generate basic statistics.
"""
import os
import sys

import pandas as pd


def analyze_csv(input_file: str, output_file: str):
    """
    Analyze CSV file and generate basic statistics report.

    Args:
        input_file: Path to input CSV file
        output_file: Path to output report file
    """
    try:
        # Read CSV file
        df = pd.read_csv(input_file)

        # Create output directory if needed
        os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)

        # Generate report
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write("CSV File Analysis Report\n")
            f.write("=" * 60 + "\n\n")

            f.write(f"Input File: {input_file}\n")
            f.write(f"Dataset Shape: {df.shape[0]} rows × {df.shape[1]} columns\n\n")

            f.write("Column Names:\n")
            for i, col in enumerate(df.columns, 1):
                f.write(f"  {i}. {col}\n")
            f.write("\n")

            f.write("Data Types:\n")
            for col, dtype in df.dtypes.items():
                f.write(f"  {col}: {dtype}\n")
            f.write("\n")

            f.write("Missing Values:\n")
            missing = df.isnull().sum()
            if missing.sum() == 0:
                f.write("  No missing values found.\n")
            else:
                for col, count in missing.items():
                    if count > 0:
                        f.write(f"  {col}: {count} ({count/len(df)*100:.1f}%)\n")
            f.write("\n")

            f.write("Summary Statistics:\n")
            f.write("-" * 60 + "\n")
            numeric_cols = df.select_dtypes(include=['number']).columns
            if len(numeric_cols) > 0:
                f.write(str(df[numeric_cols].describe()))
                f.write("\n")
            else:
                f.write("  No numeric columns found.\n")

            f.write("\n" + "=" * 60 + "\n")
            f.write("Report generated successfully.\n")

        print(f"Analysis report written to: {output_file}")

    except FileNotFoundError:
        print(f"Error: Input file not found: {input_file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error analyzing CSV file: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: analyze_csv.py <input_csv> <output_report>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    analyze_csv(input_file, output_file)
