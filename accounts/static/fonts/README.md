# CaskaydiaCove webfonts

Source: [Nerd Fonts v3.4.0, CascadiaCode](https://github.com/ryanoasis/nerd-fonts/tree/v3.4.0/patched-fonts/CascadiaCode), derived from Microsoft's Cascadia Code. The original regular and bold CaskaydiaCove Nerd Font files are distributed under the [SIL Open Font License](LICENSE). The generated files retain the font's name and embed the full license in their metadata.

These WOFF2 text subsets contain available Latin, Latin Extended, combining marks, Greek, punctuation, arrows, math operators, and box-drawing characters from the ranges below. The large private-use Nerd icon collection is omitted; UI symbols use standard Unicode and the logo is SVG. Characters outside the subset fall back to the visitor's fonts. Italic uses browser synthesis; mathematical notation uses the existing KaTeX fonts.

The two files total 109,056 bytes (about 107 KiB). Font generation is a one-time asset preparation step, not an application or build dependency.

## Reproduce

Download `CaskaydiaCoveNerdFont-Regular.ttf` and `CaskaydiaCoveNerdFont-Bold.ttf` from the pinned source above. Verify their SHA-256 hashes:

```text
701d7ec08f58f07251c1758361c5d1ab57ba0a867dd378cbb0fa52e1d2beccad  CaskaydiaCoveNerdFont-Regular.ttf
d38b2e9461f52c70ef9f18c5c79f869d8b084432416ea6e850855c5222fbdc38  CaskaydiaCoveNerdFont-Bold.ttf
```

With FontTools 4.60.0, Brotli 1.2.0, and Zopfli 0.4.3 in a temporary environment, run the helper from the repository root. It checks the source hashes, embeds the full OFL, and writes the two WOFF2 files into `design/fonts/`. Retain `LICENSE` with any copies.

```bash
python design/fonts/subset.py /path/to/downloaded/ttfs
```
