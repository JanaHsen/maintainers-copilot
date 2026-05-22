Indexing and selection
======================

pandas has three primary indexing accessors. ``df.loc[]`` is
label-based: ``df.loc['2024-01-15', 'price']`` selects a row by
index label and a column by column name. ``df.iloc[]`` is
position-based: ``df.iloc[0, 2]`` selects the first row, third
column. ``df.at[]`` and ``df.iat[]`` are scalar-fast variants of
``loc`` and ``iloc`` respectively, used in tight loops.

Boolean indexing is the workhorse of filtering. ``df[df['x'] > 0]``
returns the subset where ``x`` is positive; chains of conditions
combine with ``&``, ``|``, ``~`` (the bitwise operators —
short-circuit ``and`` / ``or`` do not work on Series because their
truthiness is ambiguous). Wrap conditions in parens when chaining
to avoid operator-precedence surprises:
``df[(df['x'] > 0) & (df['y'] < 10)]``.

MultiIndex selection has its own surface. ``df.loc[(outer, inner)]``
selects with a tuple key; ``df.loc[outer]`` returns a slice with
the outer level dropped; ``df.xs('A', level='outer')`` selects
without dropping the level. The ``df.loc[(slice(None), 'inner_val'), :]``
form selects a full cross-section.
