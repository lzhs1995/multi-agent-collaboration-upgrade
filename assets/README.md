# Project artwork

Original vector artwork for Multi-Agent Collaboration Upgrade, distributed under
the repository's MIT license. No third-party product logos are included.

- `logo.svg` / `logo.png`: four terminal workspaces, each with a supervisor pane
  and executor panes, connected through a common coordinator.
- `architecture.svg` / `architecture.png`: the intended cmux/Herdr research
  topology. The image explicitly labels the live acceptance status as pending.

SVG files include accessible titles and descriptions. PNG files are provided for
presentations and viewers without SVG support. The fixed dark background keeps
the artwork legible in both light and dark GitHub themes.

Regenerate with librsvg:

```sh
rsvg-convert assets/logo.svg -o assets/logo.png
rsvg-convert assets/architecture.svg -o assets/architecture.png
```
