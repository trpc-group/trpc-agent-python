# Pandas Quick Reference Guide

## Basic Operations

### Reading Data
```python
import pandas as pd

# Read CSV file
df = pd.read_csv('input.csv')

# Read with specific options
df = pd.read_csv('input.csv', sep=',', header=0, index_col=0)
```

### Data Inspection
```python
# View first few rows
df.head()

# View last few rows
df.tail()

# Get data shape
df.shape

# Get column names
df.columns

# Get data types
df.dtypes

# Get basic info
df.info()

# Get summary statistics
df.describe()
```

### Data Selection
```python
# Select column
df['column_name']

# Select multiple columns
df[['col1', 'col2']]

# Select rows by index
df.iloc[0:5]

# Select rows by condition
df[df['column'] > value]
```

### Data Manipulation
```python
# Add new column
df['new_col'] = df['col1'] + df['col2']

# Drop columns
df.drop('column_name', axis=1)

# Drop rows
df.drop([0, 1], axis=0)

# Fill missing values
df.fillna(0)
df.fillna(df.mean())

# Group by
df.groupby('column').sum()
df.groupby('column').mean()
```

### Statistical Functions
```python
# Basic statistics
df.mean()
df.median()
df.std()
df.min()
df.max()
df.sum()
df.count()

# Correlation
df.corr()

# Value counts
df['column'].value_counts()
```

