PYTHON = python3
SCRIPTS = scripts

.PHONY: twanksta-enumerate twanksta-fetch twanksta-parse prusaspira-fetch prusaspira-parse \
        prusaspira-extended prusaspira-extended-parse dictionary \
        release status test-twanksta-enumerate test-twanksta-fetch test-prusaspira-fetch \
        test-prusaspira-extended

# Phase 1: enumerate all words from wirdeins.twanksta.org
twanksta-enumerate:
	$(PYTHON) $(SCRIPTS)/twanksta_enumerate.py

# Phase 2: cache raw HTML for all twanksta words (entries + form tables)
twanksta-fetch:
	$(PYTHON) $(SCRIPTS)/twanksta_fetch.py

# Phase 3: cache raw HTML from prusaspira.org by letter
prusaspira-fetch:
	$(PYTHON) $(SCRIPTS)/prusaspira_fetch.py

# Phase 4: parse twanksta HTML into structured JSON
twanksta-parse:
	$(PYTHON) $(SCRIPTS)/twanksta_parse.py

# Phase 5: parse prusaspira HTML into structured JSON
prusaspira-parse:
	$(PYTHON) $(SCRIPTS)/prusaspira_parse.py

# Phase 6: fetch extended forms (comparative, superlative, participle declensions)
prusaspira-extended:
	$(PYTHON) $(SCRIPTS)/prusaspira_fetch.py --extended

# Phase 7: merge extended forms into prusaspira_entries.json
prusaspira-extended-parse:
	$(PYTHON) $(SCRIPTS)/prusaspira_extended_parse.py

# Phase 8: build the canonical prussian_dictionary.json from parsed entries
# (consumed by prussian-mcp and prussian-lora). Twanksta-only by default;
# pass WITH_PRUSASPIRA=1 to union in prusaspira-only lemmas.
dictionary:
	$(PYTHON) $(SCRIPTS)/build_dictionary.py $(if $(WITH_PRUSASPIRA),--with-prusaspira,)

# Create a versioned release archive and upload to GitHub Releases.
# Ships both the raw HTML cache and the parsed/derived artefacts that
# downstream repos consume (dictionary, per-source entries, tabula source).
release:
	@VERSION=$$(date +v%Y-%m-%d); \
	RAW_ARCHIVE=prussian_raw_$${VERSION}.tar.zst; \
	DATA_ARCHIVE=prussian_corpus_$${VERSION}.tar.zst; \
	echo "Creating $$RAW_ARCHIVE..."; \
	tar --zstd -cf $$RAW_ARCHIVE raw/; \
	echo "Creating $$DATA_ARCHIVE..."; \
	tar --zstd -cf $$DATA_ARCHIVE \
		parsed/prussian_dictionary.json \
		parsed/twanksta_entries.json \
		parsed/prusaspira_entries.json \
		data/sources/tabula.html; \
	sha256sum $$RAW_ARCHIVE $$DATA_ARCHIVE > SHA256SUMS; \
	echo "Uploading to GitHub Releases as $$VERSION..."; \
	gh release create $$VERSION $$RAW_ARCHIVE $$DATA_ARCHIVE SHA256SUMS \
		--title "Prussian corpus $$VERSION" \
		--notes "Scraped from wirdeins.twanksta.org and prusaspira.org, plus the manually-corrected tabula.html. Raw HTML in prussian_raw_*; parsed dictionary + per-source entries + tabula in prussian_corpus_*. See README.md."; \
	echo "Done: $$RAW_ARCHIVE, $$DATA_ARCHIVE"

# Status overview
status:
	@echo "=== twanksta-enumerate ===" && $(PYTHON) $(SCRIPTS)/twanksta_enumerate.py --status
	@echo "=== twanksta-fetch ===" && $(PYTHON) $(SCRIPTS)/twanksta_fetch.py --status
	@echo "=== prusaspira-fetch ===" && $(PYTHON) $(SCRIPTS)/prusaspira_fetch.py --status
	@echo "=== prusaspira-extended ===" && $(PYTHON) $(SCRIPTS)/prusaspira_fetch.py --extended-status

# Quick tests
test-twanksta-enumerate:
	$(PYTHON) $(SCRIPTS)/twanksta_enumerate.py --test

test-twanksta-fetch:
	$(PYTHON) $(SCRIPTS)/twanksta_fetch.py --test

test-prusaspira-fetch:
	$(PYTHON) $(SCRIPTS)/prusaspira_fetch.py --test

test-prusaspira-extended:
	$(PYTHON) $(SCRIPTS)/prusaspira_fetch.py --extended --test

test-prusaspira-extended-parse:
	$(PYTHON) $(SCRIPTS)/prusaspira_extended_parse.py
