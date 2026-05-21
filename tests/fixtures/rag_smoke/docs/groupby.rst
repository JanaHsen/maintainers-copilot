GroupBy: split, apply, combine
==============================

The ``GroupBy`` mechanism splits a DataFrame into groups, applies a
function to each group, and combines the results back into a single
DataFrame or Series. Users start with ``df.groupby(key)`` where
``key`` is a column name, a list of column names, a Series, or a
callable producing one of the above.

Aggregation is the most common combine step. ``df.groupby('A').sum()``
produces one row per group with summed columns. ``.agg`` accepts a
dict mapping columns to functions for per-column aggregation, and
``.agg(['mean', 'std'])`` produces a hierarchical-column result with
the requested statistics. ``.transform`` returns a DataFrame the
same shape as the original, with each row replaced by its group's
summary — useful for normalization patterns like
``df['z'] = (df['x'] - df.groupby('g')['x'].transform('mean')) / df.groupby('g')['x'].transform('std')``.

Time-based grouping uses a DatetimeIndex together with the
``Grouper`` class or the ``df.groupby(df['ts'].dt.floor('D'))``
pattern. ``resample`` is groupby's time-aware sibling and accepts
the same frequency aliases (``'D'``, ``'W'``, ``'M'``, ``'h'``).
