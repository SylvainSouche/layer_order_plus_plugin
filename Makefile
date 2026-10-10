# Advanced Layer Order (QGIS 4 fork) — build
# Single source of truth: ./VERSION
# Detailed history: VERSION.md; macro versions: metadata.txt changelog

PLUGIN_DIR   := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
PLUGIN_DIR   := $(PLUGIN_DIR:/=)
PLUGIN_NAME  := advanced_layer_order
VERSION_FILE := $(PLUGIN_DIR)/VERSION
VERSION      := $(shell tr -d '[:space:]' < "$(VERSION_FILE)")
# Zip lands in ./dist
DIST_DIR     := $(PLUGIN_DIR)/dist
ZIP_NAME     := $(PLUGIN_NAME)-$(VERSION).zip
ZIP_PATH     := $(DIST_DIR)/$(ZIP_NAME)

.PHONY: all zip sync clean show-version help check test lint qgis-test test-data

all: zip

help:
	@echo "VERSION (from ./VERSION) = $(VERSION)"
	@echo "Output zip               = $(ZIP_PATH)"
	@echo "Targets: zip sync clean show-version check lint test qgis-test test-data"

show-version:
	@echo $(VERSION)

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

check:
	python3 "$(PLUGIN_DIR)/scripts/check_plugin.py"

test:
	@bash "$(PLUGIN_DIR)/scripts/run_tests.sh"

lint:
	ruff check "$(PLUGIN_DIR)"

# End-to-end checks inside a real (headless) QGIS — see tests/qgis/
qgis-test:
	@bash "$(PLUGIN_DIR)/scripts/run_qgis_checks.sh"

# Regenerate the manual-test project (tests/data/test.qgz + test_layers.gpkg)
test-data:
	@bash "$(PLUGIN_DIR)/scripts/run_qgis_checks.sh" --script "$(PLUGIN_DIR)/tests/data/make_test_project.py"

# The plugin folder name inside the zip is the plugin's identity in QGIS
# (and on plugins.qgis.org): never derive it from the checkout folder.
PLUGIN_FOLDER := advanced_layer_order
# Runtime files only (plus the user guide and history); no dev files,
# no hidden files, no executable bits.
RUNTIME_FILES := $(sort $(wildcard $(PLUGIN_DIR)/*.py)) \
                 $(PLUGIN_DIR)/metadata.txt $(PLUGIN_DIR)/LICENSE $(PLUGIN_DIR)/icon.png \
                 $(PLUGIN_DIR)/README.md $(PLUGIN_DIR)/VERSION.md
STAGE := $(DIST_DIR)/stage

zip: sync
	@rm -rf "$(STAGE)" "$(ZIP_PATH)"
	@mkdir -p "$(STAGE)/$(PLUGIN_FOLDER)/icons" "$(STAGE)/$(PLUGIN_FOLDER)/docs"
	@cp $(RUNTIME_FILES) "$(STAGE)/$(PLUGIN_FOLDER)/"
	@cp $(PLUGIN_DIR)/icons/*.svg "$(STAGE)/$(PLUGIN_FOLDER)/icons/"
	@cp $(PLUGIN_DIR)/docs/DOCUMENTATION.md "$(STAGE)/$(PLUGIN_FOLDER)/docs/"
	@find "$(STAGE)" -type d -exec chmod 755 {} + && find "$(STAGE)" -type f -exec chmod 644 {} +
	@cd "$(STAGE)" && zip -qrX "$(ZIP_PATH)" "$(PLUGIN_FOLDER)"
	@rm -rf "$(STAGE)"
	@echo "OK: $(ZIP_PATH)"

clean:
	@rm -rf "$(DIST_DIR)"
	@echo "Removed $(DIST_DIR)"
