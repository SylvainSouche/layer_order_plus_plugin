# Layer Order Plus (QGIS 4 fork) — build
# Single source of truth: ./VERSION
# Next milestone when validated: 1.1.0

PLUGIN_DIR   := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
PLUGIN_DIR   := $(PLUGIN_DIR:/=)
PLUGIN_NAME  := layer_order_plus_qgis4
VERSION_FILE := $(PLUGIN_DIR)/VERSION
VERSION      := $(shell tr -d '[:space:]' < "$(VERSION_FILE)")
# Zip lands in ./dist (CI-friendly; also copied beside Makefile parent when useful)
DIST_DIR     := $(PLUGIN_DIR)/dist
ZIP_NAME     := $(PLUGIN_NAME)-$(VERSION).zip
ZIP_PATH     := $(DIST_DIR)/$(ZIP_NAME)

.PHONY: all zip sync clean show-version help check test

all: zip

help:
        @echo "VERSION (from ./VERSION) = $(VERSION)"
        @echo "Output zip               = $(ZIP_PATH)"
        @echo "Targets: zip sync clean show-version check test"

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

zip: sync
        @mkdir -p "$(DIST_DIR)"
        @echo "Building $(ZIP_NAME) ..."
        @rm -f "$(ZIP_PATH)"
        @# zip contents: plugin folder named layer_order_plus_qgis4 for QGIS Install from ZIP
        @cd "$(PLUGIN_DIR)/.." && zip -r "$(ZIP_PATH)" "$(notdir $(PLUGIN_DIR))" \
                -x "*__pycache__*" \
                -x "*.pyc" \
                -x "*.pyo" \
                -x "*/.DS_Store" \
                -x "*/.git/*" \
                -x "*/dist/*" \
                -x "*/.github/*" \
                -x "*/tests/*" \
                -x "*/pytest.ini" \
                -x "*/scripts/run_tests.sh" \
                -x "*/scripts/setup_libegl.sh"
        @ls -la "$(ZIP_PATH)"
        @echo "OK: $(ZIP_PATH)"

clean:
        @rm -rf "$(DIST_DIR)"
        @echo "Removed $(DIST_DIR)"
