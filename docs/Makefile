buildargs := -b dirhtml -aE source

.PHONY: cleanbuild clean serve serve_versions versions versions_git versions_index
.NOTINTERMEDIATE:
.PRECIOUS: versions/index.html versions/%/index.html checkouts/%/docs/html/index.html archived/%.tar.gz

BRANCHES := main dev
TAGS := $(shell git tag)
FILTERTED_TAGS := $(foreach tag,$(TAGS),$(shell if [[ -n "$$(git log --max-count=1 --oneline "$(tag)" -- .)" ]]; then echo "$(tag)"; fi))
VERSIONS := $(BRANCHES) $(FILTERTED_TAGS)

cleanbuild:
	@$(MAKE) clean
	@$(MAKE) html

clean:
	rm -rf html source/cli source/code .buildinfo .doctrees versions checkouts

html:
	sphinx-build $(SPHINXARGS) $(buildargs) html

serve: html
	cd html && python -m http.server 9999

checkouts/%/docs/html/index.html:
	@mkdir -p checkouts
	@# use backslashed multi-line cmd because otherwise variables will be lost
	@branch="$$(echo "$(@D)" | sed 's|^checkouts/||g;s|/docs/html$$||g')" && \
	ref="$$branch" && \
	if ! git log --max-count=1 --oneline "$$branch" >/dev/null 2>/dev/null ; then \
	  commit="$$(git ls-remote origin refs/{tags,heads}/"$$branch" | cut -f 1)" ; \
	  [[ -n "$$commit" ]] && echo "found commit $$commit for $$branch" >&2 && \
	  ref="$$commit" && git branch -f "$$branch" "$$ref" ; \
	fi && \
	[[ -n  "$$(git log --max-count=1 --oneline "$$ref" -- .)" ]] || \
	(echo "ERROR: branch '$$branch' seems to have no docs/ dir, checked ref '$$ref'" >&2 && exit 1) && \
	checkout="checkouts/$$branch" && \
	ver="$$(echo "$$branch" | sed 's|^v\([0-9]\)|\1|g')" && \
	set -x && \
	([[ -e "$$checkout/.git" ]] || git clone .. "$$checkout" ) && \
	(! [[ -e "$$checkout/docs/source/conf.py" ]] || echo "version = '$$ver'" >> "$$checkout/docs/source/conf.py") && \
	$(MAKE) -C "$$checkout/docs" SPHINXARGS="-D version=$$ver"

archived/%.tar.gz: checkouts/%/docs/html/index.html
	mkdir -p archived
	tar -C "checkouts/$*/docs/html" -czf "$@" .

versions/%/index.html: archived/%.tar.gz
	@mkdir -p "$(@D)"
	@echo "working on version '$*'"
	tar -xf "archived/$*.tar.gz" -C "$(@D)"
	@# ensure index file exists and update its timestamp for Make's dependency detection
	[[ -e "$(@)" ]] && touch "$(@)"

versions/versions.css: versjon/versions.css
	@mkdir -p versions
	cp versjon/versions.css versions/

versions_git:
	@$(MAKE) $(patsubst %, versions/%/index.html, $(VERSIONS))

versions/index.html: $(sort $(wildcard versions/*/index.html))
	rm -rf versions/stable
	@cd versions && set -x && versjon --stable-version main --user_templates ../versjon
	@# ensure the global index.html exists and is newer than each version's index.html
	[[ -e "$(@)" ]] && touch "$(@)"

versions: versions_git versions/versions.css
	@$(MAKE) versions/index.html

serve_versions: versions/index.html
	cd versions && python -m http.server 9888
