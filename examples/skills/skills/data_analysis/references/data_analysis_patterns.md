# Data Analysis Common Patterns

## Pattern 1: Basic CSV Analysis

```python
import pandas as pd
import sys

input_file = sys.argv[1]
output_file = sys.argv[2]

# Read data
df = pd.read_csv(input_file)

# Generate report
with open(output_file, 'w') as f:
    f.write(f"Dataset Shape: {df.shape}\n")
    f.write(f"Columns: {', '.join(df.columns)}\n")
    f.write("\nSummary Statistics:\n")
    f.write(str(df.describe()))
```

## Pattern 2: Column Analysis

```python
import pandas as pd
import sys

input_file = sys.argv[1]
output_file = sys.argv[2]

df = pd.read_csv(input_file)

# Analyze numeric columns
numeric_cols = df.select_dtypes(include=['number']).columns

with open(output_file, 'w') as f:
    for col in numeric_cols:
        f.write(f"\n{col}:\n")
        f.write(f"  Mean: {df[col].mean():.2f}\n")
        f.write(f"  Std: {df[col].std():.2f}\n")
        f.write(f"  Min: {df[col].min()}\n")
        f.write(f"  Max: {df[col].max()}\n")
```

## Pattern 3: Data Quality Check

```python
import pandas as pd
import sys

input_file = sys.argv[1]
output_file = sys.argv[2]

df = pd.read_csv(input_file)

with open(output_file, 'w') as f:
    f.write("Data Quality Report\n")
    f.write("=" * 50 + "\n")
    f.write(f"Total Rows: {len(df)}\n")
    f.write(f"Total Columns: {len(df.columns)}\n")
    f.write("\nMissing Values:\n")
    f.write(str(df.isnull().sum()))
    f.write("\n\nData Types:\n")
    f.write(str(df.dtypes))
```

