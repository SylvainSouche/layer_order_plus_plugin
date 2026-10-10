# Releasing Advanced Layer Order

## Every release

1. On `1.3-maintain` (or the current maintenance branch):
   * bump [`VERSION`](../VERSION) (`make sync` copies it into `metadata.txt`);
   * describe the change in [`VERSION.md`](../VERSION.md);
   * touch the `changelog` in `metadata.txt` only for a new macro version
     (e.g. `1.3.x`).
2. Run everything and build:

   ```bash
   make lint && make test && make qgis-test && make check
   make zip        # dist/advanced_layer_order-<VERSION>.zip
   ```

   `make qgis-test` runs every check under each QGIS installed on the Mac
   (official QGIS 3.40 and 4.2 apps, MacPorts QGIS 3.44). The supported
   range is 3.40 – 4.x; 4.0 relies on the shared code paths of 4.2.
3. Run the manual acceptance test ([`TEST_SCENARIO.md`](TEST_SCENARIO.md))
   with that zip, on QGIS 4 and on QGIS 3.44.
4. PR into `main`, merge, then tag `main`: `git tag v<VERSION>` and push
   the tag. Optionally publish a GitHub release with the zip attached.
5. Upload the zip on plugins.qgis.org: log in with your OSGeo ID → the
   plugin's page → **Add version**. A new version of an existing plugin
   is available once the upload's security scan passes; a **new** plugin
   needs staff approval first.

## Identity

Advanced Layer Order is published as a **new plugin** on
plugins.qgis.org: the continuation of *Layer Order Plus* by Samuel
Kultz, with his agreement. His entry (`layer_order_plus`, QGIS 3) stays
his and untouched.

| | |
|---|---|
| Plugin name (`name=` in `metadata.txt`) | Advanced Layer Order |
| Folder in the zip (the plugin's permanent identity) | `advanced_layer_order` (`PLUGIN_FOLDER` in the Makefile) |
| Repository | https://github.com/SylvainSouche/qgis_advanced_layer_order |

The first upload needs staff approval. Never change the folder name
afterwards: QGIS and plugins.qgis.org would see a different plugin.

**Installed copies from before the rename** live in a folder named
`layer_order_plus_plugin`: uninstall that once before installing
Advanced Layer Order, or QGIS shows two plugins. Projects saved with it
keep their groups (their tree is read from the old `BetterLayerOrder`
project key; it is saved under `AdvancedLayerOrder` from then on).
