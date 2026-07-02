PYTHON = python3
SCRIPTS = scripts

.PHONY: twanksta-enumerate twanksta-fetch twanksta-parse twanksta-articles-parse \
        prusaspira-fetch prusaspira-parse \
        prusaspira-extended prusaspira-extended-parse \
        youtube-fetch youtube-parse youtube-dedup \
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

# Phase 4b: extract article/description content from twanksta HTML
twanksta-articles-parse:
	$(PYTHON) $(SCRIPTS)/twanksta_articles_parse.py

# Phase 5: parse prusaspira HTML into structured JSON
prusaspira-parse:
	$(PYTHON) $(SCRIPTS)/prusaspira_parse.py

# Phase 6: fetch extended forms (comparative, superlative, participle declensions)
prusaspira-extended:
	$(PYTHON) $(SCRIPTS)/prusaspira_fetch.py --extended

# Phase 7: merge extended forms into prusaspira_entries.json
prusaspira-extended-parse:
	$(PYTHON) $(SCRIPTS)/prusaspira_extended_parse.py

# Phase 8: download Prussian subtitles from youtube.com/@prusiskataliwadasna
youtube-fetch:
	./$(SCRIPTS)/youtube_fetch.sh

# Phase 9: parse YouTube subtitles into structured JSON corpus
youtube-parse:
	$(PYTHON) $(SCRIPTS)/youtube_parse.py

# Phase 10: deduplicate YouTube segments into sentence corpus
youtube-dedup:
	$(PYTHON) $(SCRIPTS)/youtube_dedup.py

# Create a versioned release archive and upload to GitHub Releases
release:
	@VERSION=$$(date +v%Y-%m-%d); \
	ARCHIVE=prussian_corpus_$${VERSION}.tar.zst; \
	echo "Creating $$ARCHIVE..."; \
	tar --zstd -cf $$ARCHIVE --exclude='parsed/twanksta_entries.json' --exclude='parsed/prusaspira_entries.json' parsed/; \
	echo "Uploading to GitHub Releases as $$VERSION..."; \
	gh release create $$VERSION \
		parsed/twanksta_entries.json parsed/prusaspira_entries.json $$ARCHIVE \
		--title "Prussian corpus parsed data $$VERSION" \
		--notes "Parsed dictionary entries from wirdeins.twanksta.org and prusaspira.org. See README.md for details."; \
	echo "Done: $$VERSION"

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
