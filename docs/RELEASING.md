# Releasing Layer Order Plus

## Every release

1. On `1.3-maintain` (or the current maintenance branch):
   * bump [`VERSION`](../VERSION) (`make sync` copies it into `metadata.txt`);
   * describe the change in [`VERSION.md`](../VERSION.md);
   * touch the `changelog` in `metadata.txt` only for a new macro version
     (`1.3.x`, `1.4.x`, …).
2. Run everything and build:

   ```bash
   make lint && make test && make qgis-test && make check
   make zip        # dist/layer_order_plus_qgis4-<VERSION>.zip
   ```

3. Run the manual acceptance test ([`TEST_SCENARIO.md`](TEST_SCENARIO.md))
   with that zip.
4. PR into `main`, merge, then tag `main`: `git tag v<VERSION>` and push
   the tag. Optionally publish a GitHub release with the zip attached.
5. Upload the zip on plugins.qgis.org: log in with your OSGeo ID → the
   plugin's page → **Add version**. A new version of an existing plugin
   is available once the upload's security scan passes; a **new** plugin
   needs staff approval first.

## First publication: where the plugin lives

Samuel Kultz agreed to the fork being maintained by Sylvain Souche.
Before the first upload, choose between taking over his entry and
creating a new one. The choice is permanent: on plugins.qgis.org and in
QGIS, a plugin's identity is the **folder name inside the zip**.

| | Take over `layer_order_plus` | New plugin |
|---|---|---|
| What Samuel does | Adds your OSGeo user as an owner of [his entry](https://plugins.qgis.org/plugins/layer_order_plus/) (plugin page → manage owners) | Nothing |
| Folder in the zip | must be `layer_order_plus`: `make zip PLUGIN_FOLDER=layer_order_plus` (and set it in the Makefile) | your choice, e.g. `layer_order_plus_qgis4`: set `PLUGIN_FOLDER` in the Makefile |
| `name=` in `metadata.txt` | `Layer Order Plus` (unchanged) | a distinct name, e.g. `Layer Order Plus (QGIS 4)` |
| QGIS 3 users | keep 1.0.0: this version needs QGIS 4.0+ | keep using Samuel's entry |
| QGIS 4 users | see one plugin, with the history and download count | see a new plugin; Samuel's 1.0.0 doesn't install on QGIS 4 anyway |
| Approval | new version of an existing plugin | staff approval of a new plugin |

Either way, check `metadata.txt` (`repository`, `tracker`, `homepage`
point to your fork; `author`, `email`), then rebuild the zip and rerun
`make check`.

**Your own installed copy** sits in a folder named
`layer_order_plus_plugin` (the name earlier builds used). Uninstall it
once before installing a zip with a different folder name, or QGIS will
show two plugins.
