Time series
===========

pandas treats time-indexed data as a first-class case. A
DatetimeIndex carries timezone information, supports partial-string
slicing (``df['2024-01']`` selects all rows whose timestamp falls in
January 2024), and exposes a ``.dt`` accessor on Series-of-datetimes
for component access (``s.dt.year``, ``s.dt.dayofweek``,
``s.dt.tz_convert('America/New_York')``).

The most common time-series operations are resampling, rolling
windows, and time-aware joins. ``resample('1h').mean()`` bins a
DatetimeIndex into hourly buckets and aggregates per bucket;
``rolling(window='30min').mean()`` computes a 30-minute rolling
average without binning. Both accept all the same frequency aliases.

Timezones deserve a paragraph of their own. A naive DatetimeIndex
has no timezone; ``tz_localize`` attaches one (raising on
ambiguous-daylight-savings timestamps unless told otherwise),
``tz_convert`` changes the timezone without changing the underlying
instants, and ``tz_localize(None)`` drops the timezone. Mixing naive
and tz-aware series in a single operation is an error since
pandas 2.0.
