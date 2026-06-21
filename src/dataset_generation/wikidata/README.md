# Modular Multihop Benchmark Notebooks

This folder is a modular split of `multihop_benchmark_v2_clean_patch_current.ipynb`.

## Files

- `00_common_helpers.ipynb` — shared config, cache, WDQS client, schema, common builders.
- `01_*` ... `16_*` — domain-specific generators.
- `98_final_dataset_assembly.ipynb` — bilingual clean constraints patch + final build loop.
- `99_run_all_domains_and_build.ipynb` — convenience notebook that runs everything in order.

## Recommended workflow

1. Edit/run `00_common_helpers.ipynb` once.
2. Work on one domain notebook at a time, for example `11_spacecraft.ipynb` or `12_countries.ipynb`.
3. When domains are ready, run `99_run_all_domains_and_build.ipynb`.

## Notes

- Domain notebooks can be run standalone: they load `00_common_helpers.ipynb` automatically if needed.
- Some original sections were tightly coupled and remain grouped: `scientists/physicists/mathematicians`, `paintings/museums`, and `universities/airports/cars`.
- The final assembly notebook contains the bilingual patch: `query_text_en`, clean normalized `constraints`, and `gold_answer_labels_en`.
- Do not delete cache folders unless the cache schema changes; cache prevents repeated WDQS timeouts.


## Fixed loader note

This package includes `common_helpers.py` generated from `00_common_helpers.ipynb`.
Domain notebooks load this `.py` file directly instead of `%run ./00_common_helpers.ipynb`, so the environment does **not** need the `nbformat` package just to import helpers.

If you still want to use `%run` on notebooks manually, install `nbformat` first:

```bash
pip install nbformat
```

Recommended usage: open a domain notebook and run it, or use `99_run_all_domains_and_build.ipynb`, which uses `notebook_runner.py` and does not rely on `%run` magic.


## Movie / cinema refactor notes

- `01_cinema.ipynb` is now the single source of truth for the movie/screen domain.
- The old `16_cinema_kinopoisk_extension.ipynb` is deprecated and no longer run by `99_run_all_domains_and_build.ipynb`.
- Kinopoisk API calls were removed.
- IMDb-backed constraints use official IMDb non-commercial datasets (`title.basics.tsv.gz`, `title.ratings.tsv.gz`) cached locally in `out_wikidata_benchmark/imdb_cache`.
- Generated records include both `query_text_ru` and `query_text_en`, English clean constraints, English gold labels, and Russian gold labels when available.
- The generator covers regular feature films, TV series, animated films, and short films; L3-L5 include multi-criterion and actor-bridge patterns.
