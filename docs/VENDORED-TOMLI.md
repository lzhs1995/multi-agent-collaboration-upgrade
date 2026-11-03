# Vendored Tomli

The private controller requires Python 3.10+. Its isolation code and tests
both use the same vendored pure-Python Tomli parser on every version.
There is no conditional external dependency or adapter-scanning exception.

- Upstream: https://github.com/hukkin/tomli
- Version: 2.2.1
- Commit: `73c3d102eb81fe0d2b87f905df4f740f8878d8da`
- License: MIT; full text in `licenses/TOMLI-LICENSE`.
- Source was retrieved via the official GitHub API; Git blob SHA1 and
  SHA256 were checked. The upstream tag is unsigned.

## Mechanical adaptation

`src/tomli/_parser.py` becomes `scripts/upgrade_toml.py`. Its relative
imports point to the sibling flat module `upgrade_toml_re`; the public
`__all__` and `__version__` from `__init__.py` are appended.
`src/tomli/_re.py` becomes `scripts/upgrade_toml_re.py`, with the complete
typing imports and aliases from `_types.py` inlined in place of its
relative import. Original copyright and SPDX headers are retained.
No parser algorithm is changed. Both flat files enter the ordinary
transitive dependency closure and immutable source manifest.

## Unmodified upstream source SHA256

| Path | SHA256 | Bytes |
|---|---|---:|
| `LICENSE` | `b80816b0d530b8accb4c2211783790984a6e3b61922c2b5ee92f3372ab2742fe` | 1072 |
| `src/tomli/__init__.py` | `3e1370fdec8b81d9fb31c27a9eb00df32226ddd5c2ef9bebd6c546555c034a90` | 314 |
| `src/tomli/_parser.py` | `f70f0b1b48c1edfc26659581d2f5576de7a30c7725e00348271076b1c1270e50` | 25591 |
| `src/tomli/_re.py` | `b21e2c0434603bde0a259c0d22b81d73257fa906acb79d18bf3380506a510ca0` | 3171 |
| `src/tomli/_types.py` | `f864c6d9552a929c7032ace654ee05ef26ca75d21b027b801d77e65907138b74` | 254 |

Each source path is relative to the fixed upstream commit above.
Re-vendoring requires verifying those originals and reviewing the two
flat-module transformations; do not install or import a local package.
