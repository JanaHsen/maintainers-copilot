"""One-shot helper: drive /retrieve for each draft golden question,
print the top-k parent chunks (id + source + content snippet) so the
implementer can hand-pick 2-3 ground-truth chunk ids per question.

Not committed to ship — lives under tests/manual/ which is .gitignore'd
adjacent scratch space (the dir already exists in the working tree).
"""
from __future__ import annotations

import json
import sys
import urllib.request

API = "http://localhost:8000/retrieve"

# 20 implementer-drafted questions, mixed docs + issues targets.
# Each row: (question_id, question, ideal_answer, notes).
# operator_labeled flag set False for these 20.
DRAFT_20 = [
    (
        "q01",
        "How do I group a DataFrame by date and aggregate values per group?",
        "Use df.groupby(df['date'].dt.floor('D')).agg(...). The groupby user-guide section describes named aggregations and the freq aliases like 'D', 'M', 'W'.",
        "docs: doc/source/user_guide/groupby.rst, aggregation section",
    ),
    (
        "q02",
        "What is the difference between .loc and .iloc when selecting rows?",
        ".loc selects by label (index value or boolean mask); .iloc selects by integer position. Mixing the two raises IndexingError.",
        "docs: doc/source/user_guide/indexing.rst, label-based vs position-based",
    ),
    (
        "q04",
        "How do I resample a time-series to monthly frequency?",
        "Use df.resample('MS') for month-start or 'M' for month-end, then call agg/mean/sum. The timeseries user guide covers freq aliases.",
        "docs: doc/source/user_guide/timeseries.rst, resampling",
    ),
    (
        "q05",
        "How do I read a CSV file with a non-comma separator and a custom NA marker?",
        "pd.read_csv(path, sep=';', na_values=['N/A','-']). Both sep and na_values are documented under the IO Tools page.",
        "docs: doc/source/user_guide/io.rst, read_csv parameters",
    ),
    (
        "q06",
        "How do I create a MultiIndex DataFrame from a list of tuples?",
        "pd.MultiIndex.from_tuples([...], names=[...]) then pass it as index or columns to DataFrame. See the advanced-indexing user guide.",
        "docs: doc/source/user_guide/advanced.rst, MultiIndex construction",
    ),
    (
        "q08",
        "How do I drop rows that contain any missing values?",
        "df.dropna() drops rows with any NA. Pass how='all' to drop only fully-NA rows, or thresh=N to require N non-NA values. See missing-data user guide.",
        "docs: doc/source/user_guide/missing_data.rst, dropna",
    ),
    (
        "q09",
        "What's the difference between merge, join, and concat?",
        "concat stacks DataFrames along an axis; merge does SQL-style joins on keys; join is a DataFrame method that wraps merge on the index. See the merging user guide.",
        "docs: doc/source/user_guide/merging.rst, comparison section",
    ),
    (
        "q10",
        "How do I convert a string column to the categorical dtype?",
        "df['col'] = df['col'].astype('category'). Categorical can also be constructed with pd.Categorical(values, categories=..., ordered=...). See categorical user guide.",
        "docs: doc/source/user_guide/categorical.rst, conversion",
    ),
    (
        "q11",
        "When should I use apply versus map versus applymap?",
        "Series.map is element-wise on a Series; DataFrame.applymap is element-wise on a DataFrame (deprecated in favour of DataFrame.map); DataFrame.apply applies a function along an axis. See basics user guide.",
        "docs: doc/source/user_guide/basics.rst, function application",
    ),
    (
        "q13",
        "How do I write extension dtypes (e.g. Int64, string) that handle missing data without converting to float?",
        "Use the nullable integer dtype 'Int64' (capital I) or 'string' dtype — both back NA with pd.NA instead of NaN. See integer-na user guide and nullable-string-dtype.",
        "docs: doc/source/user_guide/integer_na.rst, nullable",
    ),
    (
        "q14",
        "How do I plot a DataFrame as a line chart with multiple columns on the same axes?",
        "df.plot(kind='line') plots every numeric column against the index by default; pass y=[...] to subset. See visualization user guide.",
        "docs: doc/source/user_guide/visualization.rst, line plots",
    ),
    (
        "q15",
        "How do I compute a rolling mean over a 7-day window on time-series data?",
        "df.rolling(window='7D').mean() — pass a string offset to use a time-based window. See window/rolling user guide.",
        "docs: doc/source/user_guide/window.rst, rolling time-based",
    ),
    (
        "q16",
        "How do I contribute a bug fix to pandas — what is the dev workflow?",
        "Fork on GitHub, create a feature branch, set up the dev environment per CONTRIBUTING, run pytest, open a PR. See CONTRIBUTING.md.",
        "docs: CONTRIBUTING.md, contribution workflow",
    ),
    (
        "q17",
        "Why does groupby().agg() return a DataFrame with a MultiIndex on the columns when I pass a list of aggregations?",
        "When you pass a list of aggregation functions, the resulting DataFrame has a MultiIndex on columns of (column, agg_name). Pass a dict-of-lists or named aggregation to flatten. Maintainers explain this in several resolved issues.",
        "issues: groupby multiindex columns — common Q&A",
    ),
    (
        "q18",
        "Why do I see a dtype change to float64 after I read a CSV with integer columns that contain missing values?",
        "Standard int dtypes can't hold NaN, so pandas upcasts to float. Use dtype='Int64' (nullable integer) when reading to preserve integer semantics with NA.",
        "issues: read_csv int + NaN upcast — common maintainer reply",
    ),
    (
        "q19",
        "Why is set_index slow on a large DataFrame and what's the recommended workaround?",
        "set_index can copy the underlying array; use drop=False or pre-sort the key column. Maintainers commonly point at the sort_values + set_index pattern.",
        "issues: performance — set_index speed",
    ),
    (
        "q20",
        "How does pandas decide when to copy versus when to return a view, and why does SettingWithCopyWarning appear?",
        "Chained indexing (df[mask][col] = ...) creates a temporary intermediate that may or may not be a view. The recommended fix is the single-step .loc assignment df.loc[mask, col] = ...; see the indexing user guide's 'returning a view versus copy' section.",
        "docs: doc/source/user_guide/indexing.rst, view-vs-copy section",
    ),
    (
        "q21",
        "How can I pivot a long-form DataFrame into wide-form with multiple value columns?",
        "df.pivot(index=..., columns=..., values=[...]) accepts a list of value columns; the result has a MultiIndex on columns. See reshaping user guide.",
        "docs: doc/source/user_guide/reshaping.rst, pivot section",
    ),
    (
        "q22",
        "What is the recommended way to iterate over rows of a DataFrame, and why is iterrows discouraged?",
        "iterrows returns (index, Series) pairs and is slow because each row is materialized into a Series — and dtypes get coerced to object. Prefer vectorized operations, itertuples for read-only access, or apply/transform for per-row computation.",
        "docs: doc/source/user_guide/basics.rst, iteration",
    ),
    (
        "q23",
        "How do I sort a DataFrame by multiple columns with different sort orders per column?",
        "df.sort_values(by=['a','b'], ascending=[True, False]) — both lists must be the same length. See basics user guide / sort_values.",
        "docs: doc/source/user_guide/basics.rst, sorting",
    ),
    (
        "q25",
        "How can I detect and drop duplicate rows in a DataFrame, and what does keep='last' do?",
        "df.duplicated() flags duplicate rows after the first; df.drop_duplicates() removes them. keep='first' (default) keeps the first occurrence; keep='last' keeps the last; keep=False drops every duplicate including the original.",
        "docs: doc/source/user_guide/duplicates.rst, drop_duplicates",
    ),
]

# 5 operator_labeled=true placeholder rows (ground_truth filled in T027).
PLACEHOLDER_5 = [
    (
        "q03",
        "What does the SettingWithCopyWarning mean and how do I fix it?",
        "Chained indexing creates an ambiguous view-or-copy; resolve with a single-step .loc[mask, col] = value assignment. The indexing user guide has a dedicated section.",
        "docs: doc/source/user_guide/indexing.rst, view-vs-copy",
    ),
    (
        "q07",
        "How do I handle timezone conversion when joining two DataFrames whose timestamps are tz-naive on one side and tz-aware on the other?",
        "Convert both sides to tz-aware UTC first (.dt.tz_localize('UTC') for tz-naive, .dt.tz_convert('UTC') for tz-aware) before merging. The timeseries user guide covers tz-localize/tz-convert.",
        "docs: doc/source/user_guide/timeseries.rst, time-zone handling",
    ),
    (
        "q12",
        "Why does df.append no longer exist and what should I use instead?",
        "DataFrame.append was deprecated and removed; use pd.concat([df, other], ignore_index=True). The whatsnew page records the removal; the merging guide covers concat patterns.",
        "docs: whatsnew + merging guide",
    ),
    (
        "q24",
        "How do I read a multi-sheet Excel file and merge the sheets on a common key?",
        "pd.read_excel(path, sheet_name=None) returns a dict of DataFrames keyed by sheet name; iterate and pd.merge them on the shared key column. See IO Tools / Excel.",
        "docs: doc/source/user_guide/io.rst, excel",
    ),
    (
        "q26",
        "What's the difference between a Series with object dtype and one with string dtype, and when does it matter for memory and NA handling?",
        "object dtype is a generic Python-object array (mixed types allowed; NaN is float NaN); string dtype enforces strings, uses pd.NA for missing values, and supports vectorized string ops without object overhead. See nullable-string-dtype user guide.",
        "docs: doc/source/user_guide/text.rst, string dtype",
    ),
]


def retrieve(question: str, k: int = 10) -> list[dict]:
    body = json.dumps({"question": question, "k": k}).encode("utf-8")
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["chunks"]


def main() -> None:
    print(f"{'='*80}\nDRAFT 20 — implementer-labeled\n{'='*80}\n")
    for qid, q, _ideal, notes in DRAFT_20:
        print(f"\n[{qid}] {q}")
        print(f"    notes: {notes}")
        chunks = retrieve(q, k=10)
        for i, c in enumerate(chunks):
            snippet = c["content"][:140].replace("\n", " ")
            cid = c.get("chunk_id", "?")
            stype = c.get("source_type", "?")
            sid = c.get("source_id", "?")
            print(f"  #{i+1} id={cid} {stype}:{sid} score={c['score']:.3f}")
            print(f"     {snippet}")
    print(f"\n{'='*80}\nPLACEHOLDERS 5 — operator_labeled (T027 fills)\n{'='*80}\n")
    for qid, q, ideal, notes in PLACEHOLDER_5:
        print(f"\n[{qid}] {q}")
        print(f"    ideal: {ideal[:120]}")
        print(f"    notes: {notes}")


if __name__ == "__main__":
    main()
