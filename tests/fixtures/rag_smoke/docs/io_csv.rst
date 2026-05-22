CSV input and output
====================

``pd.read_csv`` reads a comma-separated-values file (or any
delimiter via ``sep=``) into a DataFrame. The function accepts a
filesystem path, a URL, or any object with a ``read`` method.
Common arguments include ``index_col`` (column to use as the row
index), ``parse_dates`` (columns to parse as datetimes),
``dtype`` (column-to-dtype mapping, faster than letting pandas
infer), and ``chunksize`` (return a ``TextFileReader`` iterator
that yields successive DataFrames of the requested row count
instead of reading the whole file).

``DataFrame.to_csv`` writes a DataFrame back to CSV. The default
includes the row index; pass ``index=False`` to omit it. Date
formatting follows the ``date_format`` argument (a strftime
template). For high-volume writes consider Parquet
(``to_parquet``) or HDF5 (``to_hdf``); CSV is convenient and
portable but loses dtype information on round-trip.

Common pitfalls: a CSV with a leading UTF-8 BOM is silently
handled by ``read_csv`` since pandas 1.5 but the first header
name will include the BOM unless you pass ``encoding='utf-8-sig'``;
columns inferred as ``object`` dtype usually mean a mix of types
or missing-value sentinels other than NaN; and very large files
should be read with ``chunksize`` plus ``dtype`` to keep memory
bounded.
