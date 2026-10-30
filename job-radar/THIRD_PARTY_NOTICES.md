# Third-party notices

Reviewed for Job Radar 0.8.0 on 2 October 2026.

Job Radar's original source is licensed under the [MIT licence](LICENSE). Dependencies keep their own licences; Job Radar's MIT licence does not replace them. This repository is a **source-only release**: package installers obtain dependencies separately. It includes unmodified notice texts for attribution and review, but no third-party implementation files, wheels, compiled libraries, bundled Python environment or certificate/time-zone data.

## Locked runtime and development dependencies

The following 27 packages are the exact versions in [uv.lock](uv.lock). `runtime direct` means declared by Job Radar; `runtime transitive` means required by another runtime package. Development packages support the optional test suite. Conditional dependencies are included in the inventory even when not installed on every platform. `tzdata` is a direct dependency on Windows only.

| Package / upstream | Locked version | Role | Licence | Retained text |
| --- | --- | --- | --- | --- |
| [annotated-types](https://github.com/annotated-types/annotated-types) | 0.8.0 | runtime transitive | MIT | [original notices](third_party_licenses/annotated-types-0.8.0/) |
| [anyio](https://github.com/agronholm/anyio) | 4.14.2 | runtime transitive | MIT | [original notices](third_party_licenses/anyio-4.14.2/) |
| [certifi](https://github.com/certifi/python-certifi) | 2026.7.22 | runtime transitive | MPL-2.0 | [original notices](third_party_licenses/certifi-2026.7.22/) |
| [colorama](https://github.com/tartley/colorama) | 0.4.6 | runtime transitive; development transitive | BSD-3-Clause | [original notices](third_party_licenses/colorama-0.4.6/) |
| [distro](https://github.com/python-distro/distro) | 1.9.0 | runtime transitive | Apache-2.0 | [original notices](third_party_licenses/distro-1.9.0/) |
| [h11](https://github.com/python-hyper/h11) | 0.16.0 | runtime transitive | MIT | [original notices](third_party_licenses/h11-0.16.0/) |
| [httpcore](https://github.com/encode/httpcore) | 1.0.9 | runtime transitive | BSD-3-Clause | [original notices](third_party_licenses/httpcore-1.0.9/) |
| [httpx](https://github.com/encode/httpx) | 0.28.1 | runtime direct | BSD-3-Clause | [original notices](third_party_licenses/httpx-0.28.1/) |
| [idna](https://github.com/kjd/idna) | 3.18 | runtime transitive | BSD-3-Clause | [original notices](third_party_licenses/idna-3.18/) |
| [iniconfig](https://github.com/pytest-dev/iniconfig) | 2.3.0 | development transitive | MIT | [original notices](third_party_licenses/iniconfig-2.3.0/) |
| [jinja2](https://github.com/pallets/jinja/) | 3.1.6 | runtime direct | BSD-3-Clause | [original notices](third_party_licenses/jinja2-3.1.6/) |
| [jiter](https://github.com/pydantic/jiter/) | 0.16.0 | runtime transitive | MIT | [original notices](third_party_licenses/jiter-0.16.0/) |
| [markupsafe](https://github.com/pallets/markupsafe/) | 3.0.3 | runtime transitive | BSD-3-Clause | [original notices](third_party_licenses/markupsafe-3.0.3/) |
| [openai](https://github.com/openai/openai-python) | 2.49.0 | runtime direct | Apache-2.0 | [original notices](third_party_licenses/openai-2.49.0/) |
| [packaging](https://github.com/pypa/packaging) | 26.2 | development transitive | Apache-2.0 OR BSD-2-Clause | [original notices](third_party_licenses/packaging-26.2/) |
| [pluggy](https://pypi.org/project/pluggy/) | 1.6.0 | development transitive | MIT | [original notices](third_party_licenses/pluggy-1.6.0/) |
| [pydantic](https://github.com/pydantic/pydantic) | 2.13.4 | runtime direct | MIT | [original notices](third_party_licenses/pydantic-2.13.4/) |
| [pydantic-core](https://github.com/pydantic/pydantic/tree/main/pydantic-core) | 2.46.4 | runtime transitive | MIT | [original notices](third_party_licenses/pydantic-core-2.46.4/) |
| [pygments](https://github.com/pygments/pygments) | 2.20.0 | development transitive | BSD-2-Clause | [original notices](third_party_licenses/pygments-2.20.0/) |
| [pypdf](https://github.com/py-pdf/pypdf) | 6.19.0 | runtime direct | BSD-3-Clause | [original notices](third_party_licenses/pypdf-6.19.0/) |
| [pytest](https://github.com/pytest-dev/pytest) | 8.4.2 | development direct | MIT | [original notices](third_party_licenses/pytest-8.4.2/) |
| [pyyaml](https://github.com/yaml/pyyaml) | 6.0.3 | runtime direct | MIT | [original notices](third_party_licenses/pyyaml-6.0.3/) |
| [sniffio](https://github.com/python-trio/sniffio) | 1.3.1 | runtime transitive | MIT OR Apache-2.0 | [original notices](third_party_licenses/sniffio-1.3.1/) |
| [tqdm](https://github.com/tqdm/tqdm) | 4.70.0 | runtime transitive | MPL-2.0 AND MIT | [original notices](third_party_licenses/tqdm-4.70.0/) |
| [typing-extensions](https://github.com/python/typing_extensions) | 4.16.0 | runtime transitive | PSF-2.0 | [original notices](third_party_licenses/typing-extensions-4.16.0/) |
| [typing-inspection](https://github.com/pydantic/typing-inspection) | 0.4.2 | runtime transitive | MIT | [original notices](third_party_licenses/typing-inspection-0.4.2/) |
| [tzdata](https://github.com/python/tzdata) | 2026.4 | runtime direct | Apache-2.0 | [original notices](third_party_licenses/tzdata-2026.4/) |

The [machine-readable inventory](docs/dependency-inventory.json) records upstream project and source-distribution links, dependency edges, notice paths and SHA-256 hashes. Notices were copied byte-for-byte from exact-version distribution metadata and each was checked against its distribution's `RECORD` hash. Complete nested `licenses` directories and additional supplied `AUTHORS` files were retained, including the nested Apache licence shipped with `tzdata`. No licence from an unadopted integration is included.

## Terms that need particular care

- **MIT and BSD:** retain the applicable copyright, licence conditions and disclaimer when redistributing covered code. BSD packages also have the conditions stated in their individual texts; the BSD-3-Clause name-use restriction remains applicable.
- **Apache-2.0:** `distro`, `openai` and `tzdata` retain Apache terms. Redistributing covered code requires the licence and applicable attribution notices; modified files need change notices. No dependency source was modified or copied into this repository. See [Apache License 2.0, section 4](https://www.apache.org/licenses/LICENSE-2.0).
- **MPL-2.0:** `certifi` uses MPL-2.0, and `tqdm` identifies MPL-2.0 **and** MIT terms for its respective contributions. These are not all-MIT packages. Their upstream notices and exact source archive links are retained. MPL obligations concern covered files; Job Radar's separate original files can use MIT. If a later release bundles or modifies these libraries, preserve their notices, make the covered source available as required and explain where recipients can obtain it. See [Mozilla's MPL FAQ, questions 8–11](https://www.mozilla.org/en-US/MPL/2.0/FAQ/).
- **Other / alternative terms:** `typing-extensions` declares PSF-2.0 and includes a historical licence text, retained in full. `packaging` offers Apache-2.0 OR BSD-2-Clause; `sniffio` offers MIT OR Apache-2.0. Both alternatives supplied upstream are retained. `tzdata`'s package notice and Apache text are retained; its installed IANA time-zone data is not bundled here.

This review supports using these separately installed dependencies with the original MIT-licensed project. It does not relicense them or assess a future executable installer, copied implementation, modified dependency, or different dependency version. A future dependency update should refresh the inventory and the corresponding original notices.

## Build tools and separately installed programs

`pyproject.toml` declares `setuptools>=69` and `wheel` for building the Python project. These build requirements are **not pinned by `uv.lock`** and are not application runtime dependencies. Original notices for the locally available tools observed during preparation are retained separately:

- [setuptools 84.0.0](https://github.com/pypa/setuptools), MIT: [notice](third_party_licenses/build/setuptools-84.0.0/licenses/LICENSE).
- [wheel 0.48.0](https://github.com/pypa/wheel), MIT: [notice](third_party_licenses/build/wheel-0.48.0/licenses/LICENSE.txt).

These two notices are for the build projects themselves, not an inventory of their internal vendored libraries. No build tool or vendored build-tool component is distributed here. Build isolation may obtain other versions; packaging an environment or installer would require reviewing the exact resulting tool and dependency artifacts, including their bundled notices.

Python and Tcl/Tk, an optional Codex CLI, package managers such as uv/pip, and an optional local LaTeX installation are separately installed programs. They are not redistributed by this repository and are outside the 27-package lock inventory. Hosted AI services and employer APIs have their own service terms, distinct from these software licences.

## Documentation and design acknowledgements

Official API documentation and the ApplyPack idea acknowledgement are recorded in [integration notes](docs/INTEGRATIONS.md). Job Radar uses original, small integration modules; no ApplyPack, JobSpy, JobSync, ttkbootstrap or PydanticAI implementation is included.
