# Layer Order Plus (QGIS 4 fork) — build
#
# Single source of truth for the version: ./VERSION
# Next milestone when validated: 1.1.0
#
# Usage:
#   make              # sync version into metadata + build versioned zip
#   make zip          # same
#   make sync         # only write VERSION into metadata.txt
#   make clean        # remove generated versioned zips in parent dir
#   make show-version

PLUGIN_DIR   := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
PLUGIN_DIR   := $(PLUGIN_DIR:/=)
PLUGIN_NAME  := layer_order_plus_qgis4
VERSION_FILE := $(PLUGIN_DIR)/VERSION
VERSION      := $(shell tr -d '[:space:]' < "$(VERSION_FILE)")
DIST_DIR     := $(abspath $(PLUGIN_DIR)/..)
ZIP_NAME     := $(PLUGIN_NAME)-$(VERSION).zip
ZIP_PATH     := $(DIST_DIR)/$(ZIP_NAME)

.PHONY: all zip sync clean show-version help

all: zip

help:
	@echo "VERSION (from ./VERSION) = $(VERSION)"
	@echo "Output zip               = $(ZIP_PATH)"
	@echo ""
	@echo "Targets:"
	@echo "  make / make zip   sync metadata version + build $(ZIP_NAME)"
	@echo "  make sync         write VERSION into metadata.txt only"
	@echo "  make clean        remove $(PLUGIN_NAME)-*.zip from $(DIST_DIR)"
	@echo "  make show-version print version"

show-version:
	@echo $(VERSION)

# Inject version=... into metadata.txt from VERSION (unique truth)
sync: $(VERSION_FILE)
	@test -f "$(PLUGIN_DIR)/metadata.txt" || (echo "missing metadata.txt"; exit 1)
	@python3 -c "\
import re, pathlib; \
root = pathlib.Path(r'$(PLUGIN_DIR)'); \
ver = root.joinpath('VERSION').read_text(encoding='utf-8').strip(); \
meta_path = root / 'metadata.txt'; \
text = meta_path.read_text(encoding='utf-8'); \
text2, n = re.subn(r'(?m)^version=.*$$', f'version={ver}', text, count=1); \
assert n == 1, 'version= line not found in metadata.txt'; \
meta_path.write_text(text2, encoding='utf-8'); \
print(f'metadata.txt version -> {ver}')"

zip: sync
	@echo "Building $(ZIP_NAME) ..."
	@rm -f "$(ZIP_PATH)"
	@cd "$(DIST_DIR)" && zip -r "$(ZIP_NAME)" "$(PLUGIN_NAME)" \
		-x "*__pycache__*" \
		-x "*.pyc" \
		-x "*.pyo" \
		-x "*/.DS_Store" \
		-x "$(PLUGIN_NAME)/.git/*"
	@ls -la "$(ZIP_PATH)"
	@echo "OK: $(ZIP_PATH)"

clean:
	@rm -f "$(DIST_DIR)/$(PLUGIN_NAME)"-*.zip
	@echo "Removed $(PLUGIN_NAME)-*.zip from $(DIST_DIR)"
