Introduction to pandas
======================

pandas is a Python library for data analysis. It provides two primary
data structures, Series (one-dimensional) and DataFrame
(two-dimensional), built on top of NumPy arrays. Series carry an
index and a typed array of values; DataFrames are an ordered
collection of columns, each a Series, sharing a single row index.

Two design choices set the tone for the rest of the docs. First,
pandas is index-aware: arithmetic and assignment align by label, not
by position. Second, missing data is first-class: numeric arrays use
NaN, datetime arrays use NaT, and the user-facing APIs preserve
missingness through reductions and groupby operations.

Reading users typically start by constructing a DataFrame from a CSV
file with ``pd.read_csv``, inspecting it with ``head`` and ``info``,
and learning to use ``loc`` for label-based selection and ``iloc``
for positional selection. The user guide chapters that follow walk
through these primitives one at a time before getting to groupby,
reshaping, and the time-series machinery.
